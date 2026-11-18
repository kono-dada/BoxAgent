"""Persist visual episodes and their quoted atoms in the existing Jev-Mem graph."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any

from boxagent.domain.memory.visual import VisualEpisode


def store_visual_episode(system: Any, payload: object) -> dict[str, Any]:
    """Jev-Mem is a dynamic third-party boundary; validate before graph mutation."""
    from boxagent.infrastructure.memory.jev_mem.core.graph_db import EpisodeNode, Link, LinkType

    episode = VisualEpisode.from_payload(payload)
    evidence = episode.evidence
    timestamp = datetime.fromtimestamp(evidence.captured_at, timezone.utc)
    metadata = {
        "source": "visual_memory", "modality": "image", "role": "observation",
        "source_episode_status": "pending",
        "evidence_id": evidence.evidence_id, "source_event_id": evidence.source_event_id,
        "source_event_ids": [evidence.source_event_id], "session_id": evidence.session_id,
        "interaction_id": evidence.interaction_id, "captured_at": evidence.captured_at,
        "game": episode.game, "subject": episode.subject, "aspect": episode.aspect,
        "variant": episode.variant, "game_version": episode.game_version,
        "version_verified": False, "game_version_origin": "source_text",
        "extractor_version": "visual-quoted-v1",
    }
    graph, builder = system.graph_db, system.memory_builder
    existing = [node for node in graph.nodes.values()
                if node.attributes.get("evidence_id") == evidence.evidence_id]

    def member(role: str, text: str, extras: dict[str, Any]) -> Any:
        found = next((node for node in existing if node.attributes.get("episode_role") == role), None)
        if found is not None:
            return found
        node = builder._build_magma(text, timestamp=timestamp,
                                   metadata={**metadata, **extras, "episode_role": role})
        if node is None:
            raise RuntimeError("Jev-Mem 未生成经历节点")
        existing.append(node)
        return node

    atom_nodes = [member(f"atom_{i}", atom.text, {"evidence_kind": atom.kind, "quote": atom.quote})
                  for i, atom in enumerate(episode.atoms)]
    seed = member("episode", episode.summary, {"evidence_kind": "episode"})
    cell = EpisodeNode(node_id=seed.node_id, title=episode.subject or "画面经历",
                       summary=episode.summary, start_timestamp=timestamp, end_timestamp=timestamp,
                       event_count=len(atom_nodes), event_node_ids=[n.node_id for n in atom_nodes],
                       attributes=dict(seed.attributes), embedding_vector=seed.embedding_vector,
                       boundary_reason="explicit_visual_memory")
    graph.add_node(cell)
    for atom in atom_nodes:
        atom.attributes["episode_id"] = cell.node_id
        graph.update_node(atom.node_id, {"attributes": atom.attributes})
        identity = hashlib.sha256((cell.node_id + atom.node_id + "CONTAINS").encode()).hexdigest()
        if graph.get_link(identity) is None:
            graph.add_link(Link(link_id=identity, source_node_id=cell.node_id,
                                target_node_id=atom.node_id, link_type=LinkType.SEMANTIC,
                                properties={"sub_type": "CONTAINS"}))
    # Like PersonOS chains, only one named subject + one aspect can share history.
    # The relation means time order, never successful execution or causation.
    if episode.game and episode.subject and episode.aspect != "other":
        key = hashlib.sha256("\0".join((episode.game.casefold(), episode.subject.casefold(),
                                       episode.aspect, episode.variant.casefold())).encode()).hexdigest()
        cell.attributes["chain_key"] = key
        peers = [node for node in graph.nodes.values()
                 if node.attributes.get("chain_key") == key and isinstance(node, EpisodeNode)]
        peers.sort(key=lambda node: (node.start_timestamp or timestamp, node.node_id))
        for identity, link in tuple(graph.links.items()):
            if link.properties.get("visual_chain") == key:
                graph.delete_link(identity)
        for earlier, later in zip(peers, peers[1:]):
            identity = hashlib.sha256((key + earlier.node_id + later.node_id).encode()).hexdigest()
            graph.add_link(Link(link_id=identity, source_node_id=earlier.node_id,
                                target_node_id=later.node_id, link_type=LinkType.TEMPORAL,
                                properties={"sub_type": "PRECEDES", "visual_chain": key}))
        graph.update_node(cell.node_id, {"attributes": cell.attributes})
    prior_states = {node.node_id: node.attributes.get("source_episode_status", "pending")
                    for node in [cell, *atom_nodes]}
    for node in [cell, *atom_nodes]:
        node.attributes["source_episode_status"] = "committed"
        graph.update_node(node.node_id, {"attributes": node.attributes})
    try:
        system.save_memory()
    except Exception:
        for node in [cell, *atom_nodes]:
            node.attributes["source_episode_status"] = prior_states[node.node_id]
            graph.update_node(node.node_id, {"attributes": node.attributes})
        raise
    return {"memory_ids": [cell.node_id, *[n.node_id for n in atom_nodes]],
            "episode_id": cell.node_id, "evidence_id": evidence.evidence_id,
            "atom_count": len(atom_nodes)}
