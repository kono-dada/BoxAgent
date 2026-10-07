#!/usr/bin/env python3
"""Expose BoxAgent's vendored Jev-Mem through a JSONL worker protocol."""

import argparse
import contextlib
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
import sys
import traceback
from pathlib import Path


def node_payload(node):
    attributes = dict(getattr(node, "attributes", {}) or {})
    node_type = str(_enum_value(getattr(node, "node_type", "UNKNOWN")))
    content = (getattr(node, "content_narrative", "")
               or getattr(node, "summary", "")
               or getattr(node, "title", ""))
    timestamp = (getattr(node, "timestamp", None)
                 or getattr(node, "start_timestamp", None))
    return {
        "id": node.node_id,
        "type": node_type,
        "content": content,
        "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else None,
        "metadata": attributes,
    }


def _enum_value(value):
    return getattr(value, "value", value)


def inspect_node_payload(node):
    """Return only fields needed by the local dashboard."""
    attributes = getattr(node, "attributes", {}) or {}
    safe_attributes = {
        key: attributes.get(key) for key in (
            "source", "session_id", "interaction_id", "source_event_id",
            "source_event_ids", "checkpoint_id", "narrative_level", "jev_mem",
            "legacy_canonical_id", "legacy_revision", "legacy_kind",
            "legacy_source_mode",
        ) if key in attributes
    }
    content = getattr(node, "content_narrative", "") or getattr(node, "summary", "")
    title = getattr(node, "title", "")
    if title and content and title not in content:
        content = f"{title} — {content}"
    elif title and not content:
        content = title
    timestamp = (getattr(node, "timestamp", None)
                 or getattr(node, "start_timestamp", None)
                 or getattr(node, "date_time", None))
    source = attributes.get("source") if isinstance(attributes, dict) else None
    return {
        "id": str(node.node_id),
        "type": str(_enum_value(getattr(node, "node_type", "UNKNOWN"))),
        "content": str(content or "")[:8000],
        "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else (str(timestamp) if timestamp else None),
        "source": str(source)[:200] if isinstance(source, (str, int, float, bool)) else None,
        "metadata": safe_attributes,
    }


def inspect_link_payload(link):
    properties = getattr(link, "properties", {}) or {}
    subtype = properties.get("sub_type") if isinstance(properties, dict) else None
    return {
        "id": str(link.link_id),
        "source": str(link.source_node_id),
        "target": str(link.target_node_id),
        "type": str(_enum_value(getattr(link, "link_type", "UNKNOWN"))),
        "subtype": str(_enum_value(subtype)) if subtype is not None else None,
    }


def _source_event_ids(metadata):
    """Normalize provenance so automatic and explicit writes share one identity."""
    if not isinstance(metadata, dict):
        return set()
    values = []
    if metadata.get("source_event_id"):
        values.append(metadata["source_event_id"])
    values.extend(metadata.get("source_event_ids") or ())
    return {str(value).strip() for value in values if str(value).strip()}


class Worker:
    def __init__(self, cache_dir, backend):
        # Third-party initialization can print progress; stdout belongs solely
        # to the JSONL protocol, so redirect incidental output to stderr.
        with contextlib.redirect_stdout(sys.stderr):
            from boxagent.infrastructure.memory.jev_mem.api import JevMemConfig, JevMemSystem
            from boxagent.infrastructure.memory.jev_mem.core.query_engine import QueryEngine
            use_jev = backend == "jev" or (backend == "auto" and bool(os.getenv("TYPESAFE_API_KEY")))
            config = JevMemConfig(write_enabled=True, read_enabled=True,
                                  admission_enabled=True, jev_mock=not use_jev,
                                  audit_path=str(cache_dir / "decisions.jsonl"))
            self.system = JevMemSystem(cache_dir=str(cache_dir), jev_config=config)
            if (cache_dir / "graph.json").is_file():
                self.system.load_memory()
            direct_config = replace(
                config, anchor_count=min(config.anchor_count, 5),
                answer_top_k=min(config.answer_top_k, 8),
                multihop_top_k=min(config.multihop_top_k, 12),
                maximum_depth=min(config.maximum_depth, 2),
                maximum_nodes=min(config.maximum_nodes, 12),
                maximum_edges=min(config.maximum_edges, 40),
                maximum_jev_calls=min(config.maximum_jev_calls, 3),
                max_latency_seconds=min(config.max_latency_seconds, 0.30),
            )
            self.direct_query_engine = QueryEngine(
                self.system.trg_memory, self.system.memory_builder.node_index,
                jev_config=direct_config, jev_client=self.system.memory_builder.jev)
        self.cache_dir = cache_dir
        self.backend = "jev" if use_jev else "mock"

    def handle(self, request):
        operation = request.get("operation")
        if operation == "health":
            return {"status": "ready", "backend": self.backend,
                    "cache_dir": str(self.cache_dir),
                    "memory_count": len(self.system.graph_db.nodes)}
        if operation == "remember":
            observations = request.get("observations")
            if not isinstance(observations, list) or not observations:
                raise ValueError("observations must be a non-empty list")
            reused = []
            pending = []
            nodes = tuple(self.system.graph_db.nodes.values())
            for observation in observations:
                metadata = observation.get("metadata") or {}
                provenance = _source_event_ids(metadata)
                existing = next((node for node in nodes
                                 if provenance & _source_event_ids(
                                     getattr(node, "attributes", {}) or {})), None)
                if existing is None:
                    pending.append(observation)
                    continue
                # A Final User Message may first enter the automatic admission
                # queue and later trigger the explicit remember tool. They are
                # the same evidence, not two memories. Preserve one Jev node
                # and strengthen its provenance when the explicit path wins.
                attributes = getattr(existing, "attributes", {}) or {}
                combined = _source_event_ids(attributes) | provenance
                attributes["source_event_ids"] = sorted(combined)
                if metadata.get("explicit"):
                    attributes["explicit"] = True
                    attributes["source"] = metadata.get("source", "explicit")
                existing.attributes = attributes
                reused.append(existing)
            before = set(self.system.graph_db.nodes)
            with contextlib.redirect_stdout(sys.stderr):
                result = (self.system.build_memory_from_conversation(pending)
                          if pending else {"admitted": 0, "rejected": 0})
                self.system.save_memory()
            created_ids = [node_id for node_id in self.system.graph_db.nodes
                           if node_id not in before]
            return {**result,
                    "admitted": int(result.get("admitted") or 0) + len(reused),
                    "deduplicated": len(reused),
                    "created": [node_payload(self.system.graph_db.nodes[node_id])
                                for node_id in created_ids],
                    "reused": [node_payload(node) for node in reused],
                    "memory_count": len(self.system.graph_db.nodes)}
        if operation == "remember_narrative":
            checkpoint = request.get("checkpoint")
            if not isinstance(checkpoint, dict):
                raise ValueError("checkpoint must be an object")
            summary = str(checkpoint.get("summary") or "").strip()
            if not summary:
                raise ValueError("checkpoint summary must be non-empty")
            metadata = dict(request.get("metadata") or {})
            metadata.update(source="context_checkpoint",
                            checkpoint=checkpoint)
            with contextlib.redirect_stdout(sys.stderr):
                node = self.system.memory_builder._build_magma(
                    summary, timestamp=datetime.now(timezone.utc),
                    metadata=metadata)
                from boxagent.infrastructure.memory.jev_mem.core.graph_db import NodeType
                node.node_type = NodeType.NARRATIVE
                self.system.save_memory()
            return {"created": [node_payload(node)],
                    "memory_count": len(self.system.graph_db.nodes)}
        if operation == "query":
            question = request.get("question")
            top_k = request.get("top_k", 5)
            mode = request.get("mode", "deep")
            if not isinstance(question, str) or not question.strip():
                raise ValueError("question must be non-empty text")
            if type(top_k) is not int or not 1 <= top_k <= 50:
                raise ValueError("top_k must be between 1 and 50")
            if mode not in {"direct", "deep"}:
                raise ValueError("mode must be direct or deep")
            with contextlib.redirect_stdout(sys.stderr):
                engine = self.direct_query_engine if mode == "direct" else self.system.query_engine
                context, evidence = engine.query(question, top_k=top_k)
            return {"evidence": evidence, "memories": [node_payload(node) for node in context.anchor_nodes],
                    "trace": {**context.metadata, "mode": mode}}
        if operation == "inspect":
            query = request.get("query", "")
            selected_id = request.get("selected_id")
            node_limit = request.get("node_limit", 100)
            edge_limit = request.get("edge_limit", 200)
            if not isinstance(query, str) or len(query) > 500:
                raise ValueError("query must be text with at most 500 characters")
            if selected_id is not None and (not isinstance(selected_id, str)
                                            or not selected_id.strip()
                                            or len(selected_id) > 200):
                raise ValueError("selected_id must be a non-empty ID with at most 200 characters")
            if type(node_limit) is not int or not 1 <= node_limit <= 200:
                raise ValueError("node_limit must be between 1 and 200")
            if type(edge_limit) is not int or not 0 <= edge_limit <= 500:
                raise ValueError("edge_limit must be between 0 and 500")

            graph = self.system.graph_db
            sanitized_nodes = {node_id: inspect_node_payload(node)
                               for node_id, node in graph.nodes.items()}
            sanitized_links = [inspect_link_payload(link) for link in graph.links.values()]
            node_types, link_types = {}, {}
            for node in sanitized_nodes.values():
                node_types[node["type"]] = node_types.get(node["type"], 0) + 1
            for link in sanitized_links:
                link_types[link["type"]] = link_types.get(link["type"], 0) + 1

            selected_id = selected_id.strip() if isinstance(selected_id, str) else None
            if selected_id and selected_id in sanitized_nodes:
                neighbor_ids = []
                for link in sanitized_links:
                    if link["source"] == selected_id:
                        neighbor_ids.append(link["target"])
                    elif link["target"] == selected_id:
                        neighbor_ids.append(link["source"])
                chosen_ids = [selected_id]
                chosen_ids.extend(node_id for node_id in dict.fromkeys(neighbor_ids)
                                  if node_id in sanitized_nodes and node_id != selected_id)
                chosen_ids = chosen_ids[:node_limit]
                chosen = set(chosen_ids)
                result_links = [link for link in sanitized_links
                                if link["source"] in chosen and link["target"] in chosen
                                and (link["source"] == selected_id or link["target"] == selected_id)]
                matched_count = len(chosen_ids)
            else:
                needle = query.strip().casefold()
                candidates = list(sanitized_nodes.values())
                if needle:
                    candidates = [node for node in candidates if needle in " ".join(
                        str(node.get(key) or "") for key in ("content", "type", "source", "id")
                    ).casefold()]
                candidates.sort(key=lambda node: (node.get("timestamp") or "", node["id"]), reverse=True)
                matched_count = len(candidates)
                chosen_ids = [node["id"] for node in candidates[:node_limit]]
                chosen = set(chosen_ids)
                result_links = [link for link in sanitized_links
                                if link["source"] in chosen and link["target"] in chosen]

            return {
                "nodes": [sanitized_nodes[node_id] for node_id in chosen_ids],
                "edges": result_links[:edge_limit],
                "selected_id": selected_id if selected_id in sanitized_nodes else None,
                "statistics": {
                    "node_count": len(sanitized_nodes),
                    "edge_count": len(sanitized_links),
                    "matched_count": matched_count,
                    "node_types": node_types,
                    "link_types": link_types,
                },
                "truncated": matched_count > len(chosen_ids) or len(result_links) > edge_limit,
            }
        if operation == "forget":
            memory_ids = request.get("memory_ids")
            if (not isinstance(memory_ids, list) or not memory_ids or len(memory_ids) > 50
                    or any(not isinstance(item, str) or not item.strip() or len(item) > 200
                           for item in memory_ids)):
                raise ValueError("memory_ids must contain 1 to 50 non-empty IDs")
            memory_ids = list(dict.fromkeys(item.strip() for item in memory_ids))
            deleted, missing = [], []
            builder = self.system.memory_builder
            for memory_id in memory_ids:
                node = self.system.graph_db.get_node(memory_id)
                if node is None:
                    missing.append(memory_id)
                    continue
                payload = node_payload(node)
                if not self.system.graph_db.delete_node(memory_id):
                    raise RuntimeError("failed to delete graph node: " + memory_id)
                if self.system.vector_db.exists(memory_id):
                    if not self.system.vector_db.delete_vector(memory_id):
                        raise RuntimeError("failed to delete vector: " + memory_id)
                for term in list(builder.node_index):
                    builder.node_index[term].discard(memory_id)
                    if not builder.node_index[term]:
                        del builder.node_index[term]
                deleted.append(payload)
            if deleted:
                self.system.query_engine.node_index = builder.node_index
                with contextlib.redirect_stdout(sys.stderr):
                    self.system.save_memory()
                builder.jev.audit.emit("memory_forgotten", deleted_count=len(deleted),
                                       missing_count=len(missing))
            return {"deleted": deleted, "missing": missing,
                    "memory_count": len(self.system.graph_db.nodes)}
        if operation == "save":
            with contextlib.redirect_stdout(sys.stderr):
                self.system.save_memory()
            return {"saved": True, "memory_count": len(self.system.graph_db.nodes)}
        if operation == "shutdown":
            with contextlib.redirect_stdout(sys.stderr):
                self.system.save_memory()
            return {"stopped": True}
        raise ValueError("unknown operation: " + str(operation))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--backend", choices=("auto", "jev", "mock"), default="auto")
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    worker = Worker(args.cache_dir.resolve(), args.backend)
    for line in sys.stdin:
        request = None
        stop = False
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or "id" not in request:
                raise ValueError("request must be an object with id")
            result = worker.handle(request)
            response = {"id": request["id"], "ok": True, "result": result}
            stop = request.get("operation") == "shutdown"
        except Exception as exc:
            response = {"id": request.get("id") if isinstance(request, dict) else None,
                        "ok": False, "error": {"code": type(exc).__name__, "message": str(exc)}}
            traceback.print_exc(file=sys.stderr)
        print(json.dumps(response, ensure_ascii=False), flush=True)
        if stop:
            break


if __name__ == "__main__":
    main()
