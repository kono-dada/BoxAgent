"""
Temporal Resonance Graph (TRG) backend for Jev-Mem

Shared graph backend used by Jev-Mem and the MAGMA baseline that manages event nodes,
performs graph-based retrieval, and handles memory evolution.
"""

import json
import logging
import uuid
from typing import Dict, List, Optional, Any, Tuple, Union, Set
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import asyncio
from collections import defaultdict
import re

from .graph_db import (
    GraphDBInterface,
    NetworkXGraphDB,
    EventNode,
    Link,
    NodeType,
    LinkType,
    LinkSubType,
    LinkStatus,
    TraversalConstraints
)

from .vector_db import (
    VectorDBInterface,
    VectorEncoder,
    create_vector_db
)

from .keyword_enrichment import KeywordEnricher


try:
    from boxagent.infrastructure.memory.jev_mem.utils.memory_layer import LLMController
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False
    logging.warning("Original LLM controller not available. LLM features disabled.")

@dataclass
class EventExtractionResult:
    """Result of event extraction from text"""
    content_narrative: str
    entities: List[str]
    keywords: List[str]
    emotion: Optional[str]
    timestamp: Optional[datetime]
    confidence: float = 1.0

@dataclass
class QueryContext:
    """Context for a query operation"""
    query_text: str
    anchor_nodes: List[EventNode]
    traversal_paths: List[List[str]]
    narrative_context: str
    metadata: Dict[str, Any] = field(default_factory=dict)

class TemporalResonanceGraphMemory:
    """Shared multi-relational graph backend for Jev-Mem"""

    def __init__(self,
                 graph_db: Optional[GraphDBInterface] = None,
                 vector_db: Optional[VectorDBInterface] = None,
                 encoder_model: str = 'all-MiniLM-L6-v2',
                 embedding_model: str = 'minilm',
                 llm_backend: str = 'openai',
                 llm_model: str = 'gpt-4o-mini',
                 persist_dir: Optional[str] = None,
                 enable_async: bool = False,
                 encoder=None):
        """
        Initialize the graph memory backend

        Args:
            graph_db: Graph database instance (defaults to NetworkXGraphDB)
            vector_db: Vector database instance (defaults to auto-selected)
            encoder_model: Sentence transformer model name
            llm_backend: LLM backend ('openai' or 'ollama')
            llm_model: LLM model name
            persist_dir: Directory for persistence
            enable_async: Enable async processing
        """
        # Initialize databases
        self.graph_db = graph_db or NetworkXGraphDB()

        # Initialize keyword enricher
        self.keyword_enricher = KeywordEnricher()

        # Choose encoder based on embedding_model parameter
        if encoder is not None:
            self.encoder = encoder
        elif embedding_model == 'openai':
            self.encoder = VectorEncoder(model_name='text-embedding-3-small', use_openai=True)
        else:  # minilm
            self.encoder = VectorEncoder(model_name=encoder_model, use_openai=False)
        self.vector_db = vector_db or create_vector_db(
            backend="auto",
            dimension=self.encoder.dimension,
            persist_path=str(Path(persist_dir) / "vectors") if persist_dir else None
        )

        # Initialize LLM if available
        self.llm_controller = None
        if LLM_AVAILABLE and llm_backend:
            try:
                # Get API key from environment for OpenAI
                api_key = None
                if llm_backend == 'openai':
                    import os
                    api_key = os.getenv('OPENAI_API_KEY')

                self.llm_controller = LLMController(
                    backend=llm_backend,
                    model=llm_model,
                    api_key=api_key
                )
            except Exception as e:
                logging.warning(f"Failed to initialize LLM controller: {e}")

        # Configuration
        self.persist_dir = Path(persist_dir) if persist_dir else None
        self.enable_async = enable_async

        # Statistics and caching
        self.stats = {
            'events_added': 0,
            'queries_processed': 0,
            'links_created': 0,
            'causal_inferences': 0
        }

        # Async task queue
        self.async_tasks = []

        # Dual-stream memory evolution components (as per design)
        from queue import Queue
        self.consolidation_queue = Queue()  # For slow path processing
        self.pending_consolidation = []     # Events awaiting consolidation

        # Setup logging
        self.logger = logging.getLogger(__name__)

    def add_event(self, interaction_content: str,
                 timestamp: Optional[datetime] = None,
                 metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Add a new event to the memory graph

        Args:
            interaction_content: Raw interaction text
            timestamp: Event timestamp (defaults to now)
            metadata: Additional metadata

        Returns:
            Event node ID
        """
        # Extract event details using LLM
        extraction = self._extract_event(interaction_content, metadata)

        # Create event node
        event_node = EventNode(
            node_id=str(uuid.uuid4()),
            node_type=NodeType.EVENT,
            timestamp=timestamp or extraction.timestamp or datetime.now(),
            content_narrative=extraction.content_narrative,
            attributes={
                'entities': extraction.entities,
                'keywords': extraction.keywords,
                'emotion': extraction.emotion,
                'raw_content': interaction_content,
                **(metadata or {})
            }
        )

        # Enrich content with keywords before encoding
        enriched_content = self.keyword_enricher.enrich_content(
            extraction.content_narrative,
            metadata={
                'entities': extraction.entities,
                'keywords': extraction.keywords,
                'speaker': metadata.get('speaker') if metadata else None,
                'topic': metadata.get('topic') if metadata else None
            }
        )

        # Generate embedding from enriched content
        embedding = self.encoder.encode(enriched_content)
        if len(embedding.shape) == 2:
            embedding = embedding[0]  # Extract the first (and only) embedding
        event_node.embedding_vector = embedding.tolist()

        # Add to graph database
        self.graph_db.add_node(event_node)

        # Add to vector database
        self.vector_db.add_vector(
            vector_id=event_node.node_id,
            vector=embedding,
            metadata={
                'timestamp': event_node.timestamp.isoformat(),
                'keywords': extraction.keywords,
                'entities': extraction.entities
            }
        )

        # Create immediate temporal links
        self._create_temporal_links(event_node)

        # Create seed semantic links
        self._create_semantic_links(event_node)

        # Trigger async inference if enabled
        if self.enable_async:
            task = asyncio.create_task(
                self._async_causal_inference(event_node.node_id)
            )
            self.async_tasks.append(task)

        # Update statistics
        self.stats['events_added'] += 1

        self.logger.info(f"Added event node: {event_node.node_id}")
        return event_node.node_id

    def query(self, query_text: str,
             max_results: int = 10,
             constraints: Optional[TraversalConstraints] = None) -> QueryContext:
        """
        Query the memory graph

        Args:
            query_text: Query text
            max_results: Maximum number of results
            constraints: Traversal constraints

        Returns:
            QueryContext with results
        """
        # Enrich query with keywords before encoding
        enriched_query = self.keyword_enricher.enrich_query(query_text)

        # Generate query embedding from enriched query
        query_embedding = self.encoder.encode(enriched_query)
        if len(query_embedding.shape) == 2:
            query_embedding = query_embedding[0]  # Extract the first (and only) embedding

        # Find anchor nodes via vector search
        # For multi-hop and complex queries, we need more initial anchors
        # to ensure we capture all relevant facts across different nodes
        num_anchors = max_results  # Use the full requested amount
        search_results = self.vector_db.search(
            query_vector=query_embedding,
            k=num_anchors
        )

        anchor_node_ids = [result[0] for result in search_results]
        anchor_nodes = [
            self.graph_db.get_node(node_id)
            for node_id in anchor_node_ids
            if self.graph_db.get_node(node_id)
        ]

        # Set default traversal constraints
        if constraints is None:
            constraints = TraversalConstraints(
                max_depth=3,
                max_nodes=max_results * 3,
                follow_temporal=True,
                follow_semantic=True,
                follow_causal=True
            )

        # Traverse graph from anchors
        traversal_result = self.graph_db.traverse(
            start_nodes=anchor_node_ids,
            constraints=constraints
        )

        # Synthesize narrative context
        narrative_context = self._synthesize_narrative(
            traversal_result,
            query_text,
            anchor_nodes
        )

        # Update statistics
        self.stats['queries_processed'] += 1

        return QueryContext(
            query_text=query_text,
            anchor_nodes=anchor_nodes,
            traversal_paths=traversal_result.get('paths', []),
            narrative_context=narrative_context,
            metadata={
                'stats': traversal_result.get('stats', {}),
                'search_scores': [result[1] for result in search_results]
            }
        )

    def _extract_event(self, content: str,
                      metadata: Optional[Dict[str, Any]] = None) -> EventExtractionResult:
        """Extract event information from content"""
        if self.llm_controller:
            prompt = f"""Extract structured event information from the following content.
            Identify the main narrative, key entities, keywords, and emotional tone.

            Content: {content}

            Return as JSON with:
            - content_narrative: Concise narrative summary
            - entities: List of named entities (people, places, organizations)
            - keywords: List of important keywords
            - emotion: Dominant emotional tone (if any)
            """

            try:
                response = self.llm_controller.llm.get_completion(
                    prompt,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "event_extraction",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "content_narrative": {"type": "string"},
                                    "entities": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    },
                                    "keywords": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    },
                                    "emotion": {"type": ["string", "null"]}
                                },
                                "required": ["content_narrative", "entities", "keywords", "emotion"],
                                "additionalProperties": False
                            },
                            "strict": True
                        }
                    }
                )

                result = json.loads(response)
                return EventExtractionResult(
                    content_narrative=result.get("content_narrative", content),
                    entities=result.get("entities", []),
                    keywords=result.get("keywords", []),
                    emotion=result.get("emotion"),
                    timestamp=None
                )

            except Exception as e:
                self.logger.warning(f"LLM extraction failed: {e}")

        return self._simple_extract_event(content)

    def _simple_extract_event(self, content: str) -> EventExtractionResult:
        """Simple fallback event extraction without LLM"""
        # Simple keyword extraction
        words = content.split()
        keywords = [w for w in words if len(w) > 5][:5]

        # Simple entity extraction (capitalized words)
        entities = list(set(
            word for word in re.findall(r'\b[A-Z][a-z]+\b', content)
        ))[:5]

        return EventExtractionResult(
            content_narrative=content[:500],  # Truncate if too long
            entities=entities,
            keywords=keywords,
            emotion=None,
            timestamp=None
        )

    def _create_temporal_links(self, event_node: EventNode):
        """Create temporal links for a new event"""
        all_nodes = list(self.graph_db.nodes.values())
        all_nodes.sort(key=lambda n: n.timestamp if n.timestamp else datetime.min)

        for i, node in enumerate(all_nodes):
            if node.node_id == event_node.node_id and i > 0:
                prev_node = all_nodes[i - 1]

                precedes_link = Link(
                    source_node_id=prev_node.node_id,
                    target_node_id=event_node.node_id,
                    link_type=LinkType.TEMPORAL,
                    properties={
                        'sub_type': LinkSubType.PRECEDES.value,
                        'time_delta': (event_node.timestamp - prev_node.timestamp).total_seconds()
                    }
                )
                self.graph_db.add_link(precedes_link)

                succeeds_link = Link(
                    source_node_id=event_node.node_id,
                    target_node_id=prev_node.node_id,
                    link_type=LinkType.TEMPORAL,
                    properties={
                        'sub_type': LinkSubType.SUCCEEDS.value,
                        'time_delta': (event_node.timestamp - prev_node.timestamp).total_seconds()
                    }
                )
                self.graph_db.add_link(succeeds_link)

                self.stats['links_created'] += 2
                break

    def _create_semantic_links(self, event_node: EventNode, top_k: int = 3):
        """Create semantic links based on similarity"""
        if not event_node.embedding_vector:
            return

        # Find similar events
        embedding = np.array(event_node.embedding_vector, dtype=np.float32)
        similar_events = self.vector_db.search(
            query_vector=embedding,
            k=top_k + 1  # +1 because it will include itself
        )

        for similar_id, similarity, _ in similar_events:
            if similar_id == event_node.node_id:
                continue

            # Create bidirectional semantic links
            link1 = Link(
                source_node_id=event_node.node_id,
                target_node_id=similar_id,
                link_type=LinkType.SEMANTIC,
                properties={
                    'sub_type': LinkSubType.RELATED_TO.value,
                    'similarity_score': similarity
                }
            )
            self.graph_db.add_link(link1)

            link2 = Link(
                source_node_id=similar_id,
                target_node_id=event_node.node_id,
                link_type=LinkType.SEMANTIC,
                properties={
                    'sub_type': LinkSubType.RELATED_TO.value,
                    'similarity_score': similarity
                }
            )
            self.graph_db.add_link(link2)

            self.stats['links_created'] += 2

    async def _async_causal_inference(self, node_id: str):
        """Perform asynchronous causal inference"""
        await asyncio.sleep(0.1)

        try:
            node = self.graph_db.get_node(node_id)
            if not node:
                return

            neighbors = self.graph_db.get_neighbors(node_id)
            if not neighbors:
                return

            if self.llm_controller:
                causal_links = await self._infer_causality(node, neighbors)
                for link in causal_links:
                    self.graph_db.add_link(link)
                    self.stats['links_created'] += 1

                self.stats['causal_inferences'] += 1

        except Exception as e:
            self.logger.error(f"Async causal inference failed: {e}")

    def fast_path_ingestion(self, interaction: str,
                           timestamp: Optional[datetime] = None) -> str:
        """
        Fast Path: Synaptic Ingestion (Algorithm 2 from design)
        Non-blocking operations for immediate responsiveness.

        Args:
            interaction: User interaction content
            timestamp: Optional timestamp

        Returns:
            Node ID of the created event
        """
        # Segment event (fast, no LLM)
        event_node = EventNode(
            node_id=str(uuid.uuid4()),
            node_type=NodeType.EVENT,
            timestamp=timestamp or datetime.now(),
            content_narrative=interaction,
            attributes={'raw_text': interaction}
        )

        # Add to graph
        self.graph_db.add_node(event_node)

        # Get last node for temporal link
        all_nodes = list(self.graph_db.nodes.values())
        if len(all_nodes) > 1:
            # Sort by timestamp to find previous
            sorted_nodes = sorted(
                [n for n in all_nodes if n.node_id != event_node.node_id],
                key=lambda n: n.timestamp if hasattr(n, 'timestamp') else datetime.min
            )
            if sorted_nodes:
                prev_node = sorted_nodes[-1]
                # Add temporal link
                temporal_link = Link(
                    source_node_id=prev_node.node_id,
                    target_node_id=event_node.node_id,
                    link_type=LinkType.TEMPORAL,
                    properties={'sub_type': 'PRECEDES'}
                )
                self.graph_db.add_link(temporal_link)

        # Vector indexing (fast)
        if self.encoder and self.vector_db:
            embedding = self.encoder.encode(interaction)
            if len(embedding.shape) == 2:
                embedding = embedding[0]
            event_node.embedding_vector = embedding.tolist()
            # add_vectors expects List[Tuple[str, np.ndarray, Dict]]
            self.vector_db.add_vectors([(event_node.node_id, embedding, {})])

        # Enqueue for slow path
        self.consolidation_queue.put(event_node.node_id)

        self.stats['events_added'] += 1
        self.logger.info(f"Fast path ingestion: {event_node.node_id}")

        return event_node.node_id

    def slow_path_consolidation(self) -> int:
        """
        Slow Path: Structural Consolidation (Algorithm 3 from design)
        Background process that infers latent connections.

        Returns:
            Number of new edges created
        """
        edges_created = 0

        while not self.consolidation_queue.empty():
            node_id = self.consolidation_queue.get()
            node = self.graph_db.get_node(node_id)

            if not node:
                continue

            # Get local neighborhood (2 hops)
            neighbors = self._get_neighborhood(node_id, hops=2)

            if self.llm_controller and len(neighbors) > 0:
                # Infer latent connections using LLM
                new_edges = self._infer_latent_edges(node, neighbors)

                for edge in new_edges:
                    self.graph_db.add_link(edge)
                    edges_created += 1

            # Create entity links if entities detected
            if hasattr(node, 'attributes') and 'entities' in node.attributes:
                entity_edges = self._create_entity_edges(node)
                for edge in entity_edges:
                    self.graph_db.add_link(edge)
                    edges_created += 1

        self.logger.info(f"Slow path consolidation: {edges_created} edges created")
        return edges_created

    def _get_neighborhood(self, node_id: str, hops: int = 2) -> List[EventNode]:
        """Get nodes within N hops of the given node."""
        visited = set()
        to_visit = [(node_id, 0)]
        neighborhood = []

        while to_visit:
            current_id, depth = to_visit.pop(0)
            if current_id in visited or depth > hops:
                continue

            visited.add(current_id)
            if depth > 0:  # Don't include the starting node
                node = self.graph_db.get_node(current_id)
                if node:
                    neighborhood.append(node)

            if depth < hops:
                neighbors = self.graph_db.get_neighbors(current_id)
                for neighbor_node, _ in neighbors:
                    if neighbor_node.node_id not in visited:
                        to_visit.append((neighbor_node.node_id, depth + 1))

        return neighborhood

    def _infer_latent_edges(self, node: EventNode, neighbors: List[EventNode]) -> List[Link]:
        """Infer causal and entity edges using LLM."""
        edges = []

        if not self.llm_controller:
            return edges

        # Prepare prompt for LLM
        prompt = f"Analyze these events and identify causal relationships:\n\n"
        prompt += f"Main Event: {node.content_narrative}\n\n"
        prompt += "Neighboring Events:\n"
        for i, neighbor in enumerate(neighbors[:5], 1):
            content = neighbor.content_narrative if hasattr(neighbor, 'content_narrative') else str(neighbor)
            prompt += f"{i}. {content}\n"

        prompt += "\nIdentify: 1) What caused the main event? 2) What did the main event cause?"

        # Get LLM response (simplified for minimal implementation)
        # In production, parse structured response
        # For now, create basic causal links based on temporal proximity
        for neighbor in neighbors[:3]:
            if hasattr(neighbor, 'timestamp') and hasattr(node, 'timestamp'):
                if neighbor.timestamp < node.timestamp:
                    # Potential cause
                    edges.append(Link(
                        source_node_id=neighbor.node_id,
                        target_node_id=node.node_id,
                        link_type=LinkType.CAUSAL,
                        properties={'sub_type': 'LEADS_TO', 'confidence': 0.5}
                    ))

        return edges

    def _create_entity_edges(self, node: EventNode) -> List[Link]:
        """Create entity edges for detected entities."""
        edges = []

        if not hasattr(node, 'attributes') or 'entities' not in node.attributes:
            return edges

        # For each entity, create an edge
        for entity in node.attributes['entities']:
            # In a full implementation, we'd have separate entity nodes
            # For minimal change, create entity links to other nodes with same entity
            for other_id, other_node in self.graph_db.nodes.items():
                if other_id == node.node_id:
                    continue

                if (hasattr(other_node, 'attributes') and
                    'entities' in other_node.attributes and
                    entity in other_node.attributes['entities']):

                    edges.append(Link(
                        source_node_id=node.node_id,
                        target_node_id=other_id,
                        link_type=LinkType.ENTITY,
                        properties={
                            'sub_type': 'REFERS_TO',
                            'entity': entity,
                            'confidence': 0.8
                        }
                    ))
                    break  # One link per entity for now

        return edges

    async def _infer_causality(self, node: EventNode,
                              neighbors: List[Tuple[EventNode, Link]]) -> List[Link]:
        """Infer causal relationships using LLM"""
        causal_links = []

        if not self.llm_controller:
            return causal_links

        # Prepare context
        # Handle both EventNode and EpisodeNode
        current_content = node.content_narrative if hasattr(node, 'content_narrative') else node.summary if hasattr(node, 'summary') else str(node)
        context = f"Current Event: {current_content}\n\n"
        context += "Related Events:\n"
        for neighbor_node, _ in neighbors[:5]:  # Limit to 5 neighbors
            neighbor_content = neighbor_node.content_narrative if hasattr(neighbor_node, 'content_narrative') else neighbor_node.summary if hasattr(neighbor_node, 'summary') else str(neighbor_node)
            context += f"- {neighbor_content}\n"

        prompt = f"""{context}

        Analyze potential causal relationships between the current event and related events.
        Return JSON with causal relationships:
        {{
            "causal_relations": [
                {{
                    "target_event_index": 0-based index of related event,
                    "relation_type": "LEADS_TO" or "BECAUSE_OF" or "ENABLES" or "PREVENTS",
                    "confidence": 0.0-1.0,
                    "explanation": "brief explanation"
                }}
            ]
        }}
        """

        try:
            response = self.llm_controller.llm.get_completion(
                prompt,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "causal_inference",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "causal_relations": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "target_event_index": {"type": "integer"},
                                            "relation_type": {"type": "string"},
                                            "confidence": {"type": "number"},
                                            "explanation": {"type": "string"}
                                        },
                                        "required": ["target_event_index", "relation_type", "confidence", "explanation"],
                                        "additionalProperties": False
                                    }
                                }
                            },
                            "required": ["causal_relations"],
                            "additionalProperties": False
                        },
                        "strict": True
                    }
                }
            )

            result = json.loads(response)

            for relation in result.get("causal_relations", []):
                idx = relation.get("target_event_index", 0)
                if idx < len(neighbors):
                    neighbor_node, _ = neighbors[idx]
                    rel_type = relation.get("relation_type", "LEADS_TO")

                    # Map to LinkSubType
                    subtype_map = {
                        "LEADS_TO": LinkSubType.LEADS_TO,
                        "BECAUSE_OF": LinkSubType.BECAUSE_OF,
                        "ENABLES": LinkSubType.ENABLES,
                        "PREVENTS": LinkSubType.PREVENTS
                    }

                    link = Link(
                        source_node_id=node.node_id,
                        target_node_id=neighbor_node.node_id,
                        link_type=LinkType.CAUSAL,
                        properties={
                            'sub_type': subtype_map.get(rel_type, LinkSubType.LEADS_TO).value,
                            'confidence_score': relation.get("confidence", 0.5),
                            'explanation': relation.get("explanation", "")
                        }
                    )
                    causal_links.append(link)

        except Exception as e:
            self.logger.warning(f"Causal inference failed: {e}")

        return causal_links

    def _synthesize_narrative(self, traversal_result: Dict[str, Any],
                             query_text: str,
                             anchor_nodes: List[EventNode]) -> str:
        """Synthesize a narrative context from traversal results"""
        narrative_parts = []

        narrative_parts.append("=== Key Events ===")
        for node in anchor_nodes[:3]:
            if hasattr(node, 'content_narrative'):
                narrative_parts.append(f"• {node.content_narrative}")
            elif hasattr(node, 'summary'):
                narrative_parts.append(f"• {node.summary}")
            else:
                narrative_parts.append(f"• {str(node)}")

        paths = traversal_result.get('paths', [])
        if paths:
            narrative_parts.append("\n=== Event Sequences ===")
            for path in paths[:3]:
                path_narrative = []
                for node_id in path:
                    node = self.graph_db.get_node(node_id)
                    if node:
                        if hasattr(node, 'content_narrative'):
                            path_narrative.append(node.content_narrative)
                        elif hasattr(node, 'summary'):
                            path_narrative.append(node.summary)
                        else:
                            path_narrative.append(str(node))
                if path_narrative:
                    narrative_parts.append(" → ".join(path_narrative))

        causal_links = []
        for link_id, link_data in traversal_result.get('links', {}).items():
            if link_data.get('link_type') == LinkType.CAUSAL.value:
                causal_links.append(link_data)

        if causal_links:
            narrative_parts.append("\n=== Causal Relations ===")
            for link in causal_links[:5]:
                source = self.graph_db.get_node(link['source_node_id'])
                target = self.graph_db.get_node(link['target_node_id'])
                if source and target:
                    subtype = link.get('properties', {}).get('sub_type', 'RELATES_TO')
                    explanation = link.get('properties', {}).get('explanation', '')
                    source_content = source.content_narrative if hasattr(source, 'content_narrative') else source.summary if hasattr(source, 'summary') else str(source)
                    target_content = target.content_narrative if hasattr(target, 'content_narrative') else target.summary if hasattr(target, 'summary') else str(target)
                    narrative_parts.append(
                        f"• {source_content} {subtype} {target_content}"
                    )
                    if explanation:
                        narrative_parts.append(f"  ({explanation})")

        return "\n".join(narrative_parts)

    def consolidate_narrative_nodes(self, time_window_hours: int = 24):
        """
        Consolidate event chains into higher-order narrative nodes

        Args:
            time_window_hours: Time window for grouping events
        """
        time_groups = defaultdict(list)
        for node in self.graph_db.nodes.values():
            if node.node_type == NodeType.EVENT:
                window_key = node.timestamp.strftime("%Y-%m-%d %H")
                time_groups[window_key].append(node)

        for window_key, nodes in time_groups.items():
            if len(nodes) >= 3:
                narrative = self._create_narrative_node(nodes)
                if narrative:
                    self.graph_db.add_node(narrative)

                    for event in nodes:
                        link = Link(
                            source_node_id=event.node_id,
                            target_node_id=narrative.node_id,
                            link_type=LinkType.SEMANTIC,
                            properties={
                                'sub_type': LinkSubType.PART_OF.value
                            }
                        )
                        self.graph_db.add_link(link)

    def _create_narrative_node(self, event_nodes: List[EventNode]) -> Optional[EventNode]:
        """Create a narrative node from a group of events"""
        if not event_nodes:
            return None

        # Combine narratives
        # Handle both EventNode and EpisodeNode
        combined_narrative = " ".join([
            n.content_narrative if hasattr(n, 'content_narrative') else
            n.summary if hasattr(n, 'summary') else str(n)
            for n in event_nodes
        ])

        # Extract common entities and keywords
        all_entities = []
        all_keywords = []
        for node in event_nodes:
            all_entities.extend(node.attributes.get('entities', []))
            all_keywords.extend(node.attributes.get('keywords', []))

        # Count frequency
        entity_counts = defaultdict(int)
        keyword_counts = defaultdict(int)
        for e in all_entities:
            entity_counts[e] += 1
        for k in all_keywords:
            keyword_counts[k] += 1

        # Get most common
        top_entities = sorted(entity_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        top_keywords = sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        # Create narrative node
        narrative_node = EventNode(
            node_id=str(uuid.uuid4()),
            node_type=NodeType.NARRATIVE,
            timestamp=event_nodes[0].timestamp,  # Use first event's timestamp
            content_narrative=combined_narrative[:1000],  # Limit length
            attributes={
                'entities': [e[0] for e in top_entities],
                'keywords': [k[0] for k in top_keywords],
                'event_count': len(event_nodes),
                'time_span': (
                    event_nodes[-1].timestamp - event_nodes[0].timestamp
                ).total_seconds()
            }
        )

        # Generate embedding for narrative - encoder returns 2D array, we need first element
        embedding = self.encoder.encode(narrative_node.content_narrative)
        if len(embedding.shape) == 2:
            embedding = embedding[0]  # Extract the first (and only) embedding
        narrative_node.embedding_vector = embedding.tolist()

        return narrative_node

    def save(self, path: Optional[str] = None):
        """Save the graph memory backend to disk"""
        save_path = Path(path) if path else self.persist_dir
        if not save_path:
            raise ValueError("No save path provided")

        save_path.mkdir(parents=True, exist_ok=True)

        if isinstance(self.graph_db, NetworkXGraphDB):
            self.graph_db.export_to_json(str(save_path / "graph.json"))

        self.vector_db.save(str(save_path / "vectors"))

        with open(save_path / "stats.json", 'w') as f:
            json.dump(self.stats, f, indent=2)

        self.logger.info(f"TRG memory saved to {save_path}")

    def load(self, path: Optional[str] = None):
        """Load the graph memory backend from disk"""
        load_path = Path(path) if path else self.persist_dir
        if not load_path or not load_path.exists():
            raise ValueError(f"Load path {load_path} does not exist")

        # Load graph database
        graph_file = load_path / "graph.json"
        if graph_file.exists() and isinstance(self.graph_db, NetworkXGraphDB):
            self.graph_db.import_from_json(str(graph_file))

        # Load vector database
        vector_path = load_path / "vectors"
        if vector_path.exists():
            self.vector_db.load(str(vector_path))

        # Load statistics
        stats_file = load_path / "stats.json"
        if stats_file.exists():
            with open(stats_file, 'r') as f:
                self.stats = json.load(f)

        self.logger.info(f"TRG memory loaded from {load_path}")

    def get_statistics(self) -> Dict[str, Any]:
        """Get system statistics"""
        return {
            **self.stats,
            'total_nodes': self.graph_db.size() if hasattr(self.graph_db, 'size') else len(self.graph_db.nodes),
            'total_vectors': self.vector_db.size(),
            'node_types': self._count_node_types(),
            'link_types': self._count_link_types()
        }

    def _count_node_types(self) -> Dict[str, int]:
        """Count nodes by type"""
        counts = defaultdict(int)
        for node in self.graph_db.nodes.values():
            counts[node.node_type.value] += 1
        return dict(counts)

    def _count_link_types(self) -> Dict[str, int]:
        """Count links by type"""
        counts = defaultdict(int)
        for link in self.graph_db.links.values():
            counts[link.link_type.value] += 1
        return dict(counts)

    async def wait_for_async_tasks(self):
        """Wait for all async tasks to complete"""
        if self.async_tasks:
            await asyncio.gather(*self.async_tasks)
            self.async_tasks.clear()
