"""Jev routing and batched traversal over MAGMA's existing graph and vectors."""

import time
import math
import hashlib
from dataclasses import asdict

import numpy as np

from .graph_db import LinkStatus
from .jev_client import CallBudget
from .jev_questions import routing_questions, stopping_questions, traversal_questions
from .jev_mem_policies import (
    GraphNeeds,
    allocate_graph_budgets,
    cosine,
    is_retrievable_node,
    node_state,
    timestamp_value,
)
from .trg_memory import QueryContext


class RetrievalController:
    def __init__(self, engine, client, config):
        self.engine, self.client, self.config = engine, client, config

    def query(self, question, top_k):
        if top_k < 1:
            raise ValueError("top_k must be positive")
        queued = time.monotonic()
        with self.client.retrieval_slot():
            queue_seconds = time.monotonic() - queued
            context, answer_context = self._query(question, top_k)
            context.metadata["retrieval_queue_seconds"] = queue_seconds
            return context, answer_context

    def _query(self, question, top_k):
        started = time.monotonic()
        cfg, trg = self.config, self.engine.trg
        budget = CallBudget(cfg.maximum_jev_calls, started + cfg.max_latency_seconds)
        prompts = routing_questions()
        from .temporal_parser import TemporalParser
        temporal_query = TemporalParser().is_temporal_question(question)
        intent = self.engine.detect_query_intent(question)
        baseline = dict(semantic=0.7, temporal=0.8 if intent == "WHEN" else 0.2,
                        causal=0.8 if intent == "WHY" else 0.2, entity=0.6,
                        multi_hop_need=0.5, recency_importance=0.2)
        route = self.client.evaluate("routing", {"query": question}, prompts, mock_values=baseline, budget=budget)
        needs = GraphNeeds(**(route.values if route else baseline))
        depth_limit = min(cfg.maximum_depth, max(1, math.ceil(cfg.maximum_depth * needs.multi_hop_need)))
        allocations = allocate_graph_budgets(needs, cfg)
        # Preserve the existing edge ablations as well as independent write/read switches.
        if self.engine.ablation_config.get("no_causal"):
            allocations["causal"] = 0
        if self.engine.ablation_config.get("no_temporal"):
            allocations["temporal"] = 0
        if self.engine.ablation_config.get("basic_retrieval"):
            allocations = dict.fromkeys(allocations, 0)
        used = dict.fromkeys(allocations, 0)
        nodes, scores, paths = {}, {}, {}
        edges_examined = depth = 0
        stop_reason = "no_evidence"
        stopping = []
        query_vector = None
        # Use MAGMA's vector anchors without running its uncontrolled traversal first.
        if budget.remaining_seconds() > 0:
            enriched = trg.keyword_enricher.enrich_query(question)
            if hasattr(trg.encoder, "encode_with_timeout"):
                embedding = trg.encoder.encode_with_timeout(enriched, budget.remaining_seconds())
            else:
                embedding = trg.encoder.encode(enriched)
            query_vector = np.asarray(embedding).reshape(-1)
            if budget.remaining_seconds() > 0:
                ranked = []
                vectors = trg.vector_db.search(query_vector, k=min(cfg.maximum_nodes, max(top_k, cfg.anchor_count)))
                vector_nodes = [trg.graph_db.get_node(node_id) for node_id, _, _ in vectors]
                ranked.append([n for n in vector_nodes
                               if n is not None and is_retrievable_node(n)])
                keyword_search = self.engine._temporal_keyword_search if temporal_query else self.engine._keyword_search
                ranked.append(keyword_search(question, limit=cfg.maximum_nodes))
                anchors = self.engine._rrf_fusion(ranked)
                for node, _ in anchors:
                    if not is_retrievable_node(node):
                        continue
                    nodes[node.node_id] = node
                    scores[node.node_id] = cosine(query_vector, node.embedding_vector)
                    paths[node.node_id] = [node.node_id]
                    if len(nodes) >= min(cfg.anchor_count, cfg.maximum_nodes, top_k):
                        break
        frontier = list(nodes.values())
        # Evaluate sufficiency on the actual evidence that will be sent to System Two.
        while nodes:
            selected = sorted(nodes, key=lambda key: (-scores[key], key))[:top_k]
            if budget.remaining_seconds() <= 0:
                stop_reason = "max_latency"
                break
            if cfg.stopping_enabled:
                prompts = stopping_questions()
                decision = self.client.evaluate("stopping", {"query": question,
                    "evidence": [node_state(nodes[key], include_temporal=temporal_query) for key in selected], "depth": depth}, prompts,
                    mock_values=dict(evidence_sufficient=0.9 if depth else 0.3, continue_useful=0.2 if depth else 0.8,
                                     missing_evidence=0.1 if depth else 0.7, contradiction=0.0), budget=budget)
                if decision:
                    s = decision.values
                    stopping.append(s)
                    if (s["evidence_sufficient"] >= cfg.evidence_sufficient_threshold
                            and s["missing_evidence"] < cfg.continue_threshold and s["contradiction"] < cfg.continue_threshold):
                        stop_reason = "evidence_sufficient"
                        break
                    if s["continue_useful"] < cfg.continue_threshold:
                        stop_reason = "further_retrieval_unhelpful"
                        break
            if budget.calls >= cfg.maximum_jev_calls:
                stop_reason = "max_jev_calls"
                break
            if len(nodes) >= cfg.maximum_nodes:
                stop_reason = "max_nodes"
                break
            if depth >= depth_limit:
                stop_reason = "max_depth"
                break
            if not any(used[g] < allocations[g] for g in allocations):
                stop_reason = "graph_budget_exhausted"
                break
            proposals = {}
            for parent in frontier:
                for node, link in trg.graph_db.get_neighbors(parent.node_id):
                    if edges_examined >= cfg.maximum_edges or budget.remaining_seconds() <= 0:
                        break
                    edges_examined += 1
                    graph = link.link_type.value.lower()
                    if (node.node_id in nodes or not is_retrievable_node(node)
                            or used[graph] >= allocations[graph]):
                        continue
                    if link.metadata.get("status") not in (None, LinkStatus.ACTIVE, "ACTIVE"):
                        continue
                    # A graph budget counts candidate expansions, shared across all rounds.
                    used[graph] += 1
                    structural = float(link.properties.get("probability", link.properties.get("confidence", 0.5)))
                    structural = max(0.0, min(1.0, structural))
                    item = (node, link, parent.node_id, graph, structural)
                    previous = proposals.get(node.node_id)
                    if previous is None or structural > previous[4]:
                        proposals[node.node_id] = item
                if edges_examined >= cfg.maximum_edges or budget.remaining_seconds() <= 0:
                    break
            if budget.remaining_seconds() <= 0:
                stop_reason = "max_latency"
                break
            if not proposals:
                stop_reason = "max_edges" if edges_examined >= cfg.maximum_edges else "frontier_exhausted"
                break
            items = list(proposals.values())
            questions, defaults = {}, {}
            for i, (node, link, parent, graph, structural) in enumerate(items):
                prefix = f"candidate_{i}_"
                prompts = traversal_questions(i)
                for field, prompt in prompts.items():
                    questions[prefix + field] = prompt
                    defaults[prefix + field] = cosine(query_vector, node.embedding_vector) if field == "relevance" else 0.5
            result = self.client.evaluate("traversal", {"query": question,
                "evidence": [node_state(nodes[key], include_temporal=temporal_query) for key in selected],
                "candidates": [{**node_state(n, include_temporal=temporal_query), "relation": link.to_dict()["properties"],
                                "graph": graph, "source_id": link.source_node_id, "target_id": link.target_node_id}
                               for n, link, _, graph, _ in items]}, questions, mock_values=defaults, budget=budget)
            if budget.remaining_seconds() <= 0:
                stop_reason = "max_latency"
                break
            values = result.values if result else defaults
            weighted = []
            for i, (node, link, parent, graph, structural) in enumerate(items):
                p = f"candidate_{i}_"
                similarity = cosine(query_vector, node.embedding_vector)
                novelty = values[p + "new_information"]
                relation_fit = getattr(needs, graph) * values[p + "relation_usefulness"]
                support = (structural + values[p + "supports_current_evidence"]) / 2
                components = (similarity, values[p + "relevance"], relation_fit, novelty, support)
                score = sum(w * s for w, s in zip(cfg.transition_weights, components)) / sum(cfg.transition_weights)
                observed_times = [timestamp_value(n.timestamp) for n in nodes.values() if n.timestamp is not None]
                candidate_time = timestamp_value(node.timestamp)
                if observed_times and candidate_time is not None:
                    recency = 1 / (1 + max(0, max(observed_times) - candidate_time) / 86400)
                    score = (score + 0.1 * needs.recency_importance * recency) / (1 + 0.1 * needs.recency_importance)
                weighted.append((score, node.node_id, node, parent))
            weighted.sort(key=lambda item: (-item[0], item[1]))
            frontier = []
            width = min(cfg.beam_width, cfg.maximum_nodes - len(nodes))
            for score, node_id, node, parent in weighted[:width]:
                nodes[node_id], scores[node_id] = node, score
                paths[node_id] = paths[parent] + [node_id]
                frontier.append(node)
            depth += 1
            stop_reason = "frontier_exhausted"
            if not frontier:
                break
        if budget.remaining_seconds() <= 0:
            stop_reason = "max_latency"
        selected = sorted(nodes, key=lambda key: (-scores[key], key))[:top_k]
        chosen = [nodes[key] for key in selected]
        metadata = {"controller": "jev-mem", "decision_backend": cfg.decision_backend, "query_id": hashlib.sha256(question.encode()).hexdigest()[:16],
                    "graph_needs": asdict(needs), "graph_budgets": allocations,
                    "graph_budget_used": used, "nodes_visited": len(nodes), "edges_examined": edges_examined,
                    "jev_calls": budget.calls, "jev_cache_hits": budget.cache_hits, "llm_calls": 0,
                    "retrieval_depth": depth, "depth_limit": depth_limit, "latency_seconds": time.monotonic() - started,
                    "stopping_decision": stop_reason, "stopping_scores": stopping,
                    "fallback_events": budget.fallback_events, "top_k_returned": len(chosen)}
        metadata["retrieved_dia_ids"] = [n.attributes.get("dia_id") for n in chosen if n.attributes.get("dia_id")]
        if temporal_query:
            metadata["temporal_evidence_policy"] = "anchored-dialogue-v1"
        answer_context = self.engine.answer_formatter.format_context_for_qa(chosen, question)
        context = QueryContext(question, chosen, [paths[key] for key in selected], answer_context, metadata)
        self.client.audit.emit("query", **metadata)
        trg.stats['queries_processed'] += 1
        return context, answer_context
