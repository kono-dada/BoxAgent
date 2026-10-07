"""Typed System-One decisions; graph nodes and edges remain MAGMA objects."""

from dataclasses import dataclass
from datetime import timezone

import numpy as np

from .graph_db import EventNode, Link, LinkType, NodeType


@dataclass(frozen=True)
class MemoryAdmission:
    should_store: float
    future_utility: float
    importance: float
    novelty: float
    redundancy: float

    def score(self, weights):
        a, b, c, d = weights
        utility = (a * self.future_utility + b * self.importance + c * self.novelty) / (a + b + c)
        return self.should_store * max(0.0, utility - d * self.redundancy)


@dataclass(frozen=True)
class MemoryTypeScores:
    episodic: float
    semantic: float
    procedural: float
    preference: float


@dataclass(frozen=True)
class GraphNeeds:
    semantic: float
    temporal: float
    causal: float
    entity: float
    multi_hop_need: float
    recency_importance: float


def timestamp_value(timestamp):
    """Normalize legacy naive dates as UTC for consistent ordering."""
    if timestamp is None:
        return None
    return timestamp.replace(tzinfo=timestamp.tzinfo or timezone.utc).timestamp()


def node_state(node, *, include_temporal=False):
    content = (getattr(node, "content_narrative", "")
               or getattr(node, "summary", "")
               or getattr(node, "title", ""))
    timestamp = (getattr(node, "timestamp", None)
                 or getattr(node, "start_timestamp", None))
    state = {"id": node.node_id, "content": content,
            "timestamp": timestamp.isoformat() if timestamp else None,
            "entities": node.attributes.get("entities", [])}
    if include_temporal:
        from .temporal_parser import TemporalParser
        state.update(timestamp_role="observation_time; not necessarily the event date",
                     temporal_references=TemporalParser().describe_references(
                         content, timestamp))
    return state


def is_retrievable_node(node):
    """Raw observations and their derived episode/narrative projections are evidence."""
    return getattr(node, "node_type", None) in {
        NodeType.EVENT, NodeType.EPISODE, NodeType.NARRATIVE,
    }


def cosine(left, right):
    if left is None or right is None:
        return 0.0
    a, b = np.asarray(left).reshape(-1), np.asarray(right).reshape(-1)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.clip(np.dot(a, b) / norm, 0, 1)) if norm else 0.0


def find_candidates(trg, node, top_k):
    """Bound the Jev candidate set using existing vector, keyword, entity and time signals."""
    ranked = {}
    if node.embedding_vector is not None:
        for node_id, _, _ in trg.vector_db.search(np.asarray(node.embedding_vector), k=top_k):
            candidate = trg.graph_db.get_node(node_id)
            if candidate is not None:
                ranked[node_id] = 2.0 * cosine(node.embedding_vector, candidate.embedding_vector)
    entities = set(node.attributes.get("entities", []))
    keywords = set(trg.keyword_enricher.extract_keywords(node.content_narrative))
    moment = timestamp_value(node.timestamp)
    for candidate in trg.graph_db.nodes.values():
        if candidate.node_type != NodeType.EVENT or candidate.node_id == node.node_id:
            continue
        score = ranked.get(candidate.node_id, 0.0)
        score += 2.0 * bool(entities & set(candidate.attributes.get("entities", [])))
        other_words = set(candidate.attributes.get("keywords", []))
        score += len(keywords & other_words) / max(1, len(keywords))
        other_time = timestamp_value(candidate.timestamp)
        if moment is not None and other_time is not None:
            score += 0.25 / (1 + abs(moment - other_time) / 86400)
        ranked[candidate.node_id] = score
    ids = sorted(ranked, key=lambda key: (-ranked[key], key))
    return [trg.graph_db.get_node(key) for key in ids
            if key != node.node_id and trg.graph_db.get_node(key).node_type == NodeType.EVENT][:top_k]


class WritePolicy:
    def __init__(self, client, config):
        self.client, self.config = client, config

    def memory_type(self, content):
        """Classify an observation without asking whether it should be stored."""
        from .jev_questions import memory_type_questions
        result = self.client.evaluate("memory_type", {"observation": content}, memory_type_questions(),
            mock_values=dict(episodic=0.8, semantic=0.7, procedural=0.1,
                preference=0.9 if any(w in content.lower() for w in (
                    "prefer", "like", "favorite", "喜欢", "偏好", "不吃", "不要")) else 0.1))
        return MemoryTypeScores(**result.values) if result is not None else None

    def assess_observation(self, content, duplicate=False, recent_memories=None):
        """One shared-state request for admission and independent type scores."""
        from .jev_questions import observation_questions
        questions = observation_questions()
        # Upstream's whitespace-only heuristic rejects ordinary Chinese text,
        # where a full preference sentence often contains no spaces.  The
        # deterministic fallback is test/degraded-mode behavior; live Jev
        # still decides from the typed questions above.
        normalized = content.strip()
        contains_cjk = any("\u4e00" <= character <= "\u9fff"
                           for character in normalized)
        meaningful = (len(content.split()) >= 4
                      or (contains_cjk and len(normalized) >= 6)
                      or len(normalized) >= 12)
        mock = dict(should_store=0.95 if meaningful else 0.05, future_utility=0.9,
                    importance=0.8, novelty=0.0 if duplicate else 0.9, redundancy=float(duplicate),
                    episodic=0.8, semantic=0.7, procedural=0.1,
                    preference=0.9 if any(w in content.lower() for w in (
                        "prefer", "like", "favorite", "喜欢", "偏好", "不吃", "不要")) else 0.1)
        result = self.client.evaluate("observation", {"observation": content, "exact_duplicate": duplicate,
                                     "recent_memories": recent_memories or []}, questions, mock_values=mock)
        if result is None:
            return None
        admission = MemoryAdmission(**{key: result.values[key] for key in MemoryAdmission.__dataclass_fields__})
        memory_type = MemoryTypeScores(**{key: result.values[key] for key in MemoryTypeScores.__dataclass_fields__})
        return admission, memory_type

    def relations(self, node, candidates):
        if not candidates:
            return []
        from .jev_questions import relation_questions
        questions, mock = {}, {}
        for i, other in enumerate(candidates):
            prefix = "pair_" + str(i) + "_"
            exact_entities = set(node.attributes["entities"]) & set(other.attributes.get("entities", []))
            prompts = relation_questions(i, infer_identity=not exact_entities)
            for name, question in prompts.items():
                key = prefix + name
                questions[key] = question
                mock[key] = cosine(node.embedding_vector, other.embedding_vector) if name == "semantic" else 0.0
        result = self.client.evaluate("relations", {"new_memory": node_state(node),
                                     "candidates": [node_state(n) for n in candidates]}, questions, mock_values=mock)
        if result is None:
            return None
        links = []
        for i, other in enumerate(candidates):
            prefix = "pair_" + str(i) + "_"
            def add(kind, subtype, probability, reverse=False, origin="jev"):
                if probability >= self.config.relation_threshold:
                    source, target = (other, node) if reverse else (node, other)
                    links.append(Link(source_node_id=source.node_id, target_node_id=target.node_id,
                                      link_type=kind, properties={"sub_type": subtype, "confidence": probability,
                                      "probability": probability}, metadata={"controller": "jev-mem", "origin": origin}))
            values = result.values
            add(LinkType.SEMANTIC, "RELATED_TO", values[prefix + "semantic"], origin=result.source)
            add(LinkType.CAUSAL, "LEADS_TO", values[prefix + "causes"], origin=result.source)
            add(LinkType.CAUSAL, "LEADS_TO", values[prefix + "caused_by"], reverse=True, origin=result.source)
            exact = set(node.attributes["entities"]) & set(other.attributes.get("entities", []))
            add(LinkType.ENTITY, "SHARED_ENTITY", 1.0 if exact else values[prefix + "entity"],
                origin="exact_identifier" if exact else result.source)
        return links


def allocate_graph_budgets(needs, config):
    """Largest-remainder integer allocation; the total never exceeds B."""
    graphs = ("semantic", "temporal", "causal", "entity")
    budgets = dict.fromkeys(graphs, 0)
    active = [g for g in graphs if getattr(needs, g) > 0 and getattr(needs, g) >= config.graph_activation_threshold]
    if not active or not config.total_graph_budget:
        return budgets
    active.sort(key=lambda g: (-getattr(needs, g), g))
    minimum = config.minimum_graph_budget
    if minimum:
        # A small budget can activate only the highest-need graphs.
        active = active[:max(1, config.total_graph_budget // minimum)]
        minimum = min(minimum, config.total_graph_budget)
    for g in active:
        budgets[g] = minimum
    remaining = config.total_graph_budget - sum(budgets.values())
    largest = max(getattr(needs, g) for g in active)
    weights = {g: (getattr(needs, g) / largest) ** config.probability_exponent for g in active}
    total = sum(weights.values())
    shares = {g: remaining * weights[g] / total for g in active}
    for g in active:
        budgets[g] += int(shares[g])
    for g in sorted(active, key=lambda g: (-(shares[g] % 1), g))[:config.total_graph_budget - sum(budgets.values())]:
        budgets[g] += 1
    return budgets
