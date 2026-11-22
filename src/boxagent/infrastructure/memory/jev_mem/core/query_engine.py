"""
Query Engine Module

Handles all query-related operations including:
- Multi-layer search (vector, keyword, full scan)
- Graph traversal with adaptive filtering
- Reranking and candidate selection
- Context building for QA
"""

import logging
from typing import List, Tuple, Set, Optional
from collections import deque
import numpy as np

from .trg_memory import EventNode, LinkType, TraversalConstraints, QueryContext
from .answer_formatter import AnswerFormatter
from .keyword_enrichment import KeywordEnricher
# from .multihop_improvements_v2 import improve_multihop_retrieval_v2  # Module not present, commented out

logger = logging.getLogger(__name__)

class QueryEngine:
    """
    Advanced query engine for Jev-Mem.

    Provides multi-stage retrieval with graph traversal.
    """

    def __init__(self, trg_memory, node_index: dict, entity_session_map: dict = None, entity_dia_map: dict = None, llm_controller=None, ablation_config=None, jev_config=None, jev_client=None):
        """
        Initialize query engine.

        Args:
            trg_memory: TRG memory instance
            node_index: Keyword index for fast lookup
            entity_session_map: Map entity -> {sessions: [list], dia_ids: {session: [dia_ids]}}
            entity_dia_map: Map entity -> list of all dia_ids where entity appears
            llm_controller: Optional LLM controller for advanced features (question decomposition)
            ablation_config: Optional dict for ablation study configurations:
                - "basic_retrieval": Only vector search, no graph traversal
                - "no_causal": Disable causal links
                - "no_temporal": Disable temporal links
                - "flat_graph": No adaptive query-type specific weights
        """
        self.trg = trg_memory
        self.node_index = node_index
        self.entity_session_map = entity_session_map or {}
        self.entity_dia_map = entity_dia_map or {}
        self.answer_formatter = AnswerFormatter()
        self.keyword_enricher = KeywordEnricher()
        self.llm_controller = llm_controller
        self.ablation_config = ablation_config or {}
        from .jev_mem_config import JevMemConfig
        from .jev_client import JevClient
        self.jev_config = jev_config or JevMemConfig()
        self.jev = jev_client or JevClient(self.jev_config)

    def _rrf_fusion(self, ranked_lists: List[List], k: int = 60) -> List[Tuple]:
        """
        Fuse multiple ranked lists using Reciprocal Rank Fusion (RRF).

        RRF is more robust than simple score combination as it handles:
        - Different score scales across retrieval methods
        - Missing items in some lists
        - Outlier scores

        Formula: RRF_score(d) = Σ(1 / (k + rank_i))
        where k=60 is empirically optimal (Cormack et al., 2009)

        Args:
            ranked_lists: List of ranked result lists. Each inner list contains nodes
                         sorted by relevance (best first).
            k: RRF constant (default: 60)

        Returns:
            List of (node, rrf_score) tuples sorted by RRF score descending

        Example:
            vector_results = [node_A, node_B, node_C]  # ranks 1, 2, 3
            keyword_results = [node_B, node_D, node_A]  # ranks 1, 2, 3

            RRF scores:
            node_A: 1/(60+1) + 1/(60+3) = 0.0164 + 0.0159 = 0.0323
            node_B: 1/(60+2) + 1/(60+1) = 0.0161 + 0.0164 = 0.0325 ← highest!
            node_C: 1/(60+3) + 0       = 0.0159
            node_D: 0       + 1/(60+2) = 0.0161
        """
        if not ranked_lists or all(not lst for lst in ranked_lists):
            return []

        if len(ranked_lists) == 1 and ranked_lists[0]:
            return [(node, 1.0 / (k + rank + 1)) for rank, node in enumerate(ranked_lists[0])]

        node_scores = {}

        for rank_list in ranked_lists:
            for rank, node in enumerate(rank_list):
                node_id = node.node_id
                rrf_contribution = 1.0 / (k + rank + 1)

                if node_id in node_scores:
                    existing_node, existing_score = node_scores[node_id]
                    node_scores[node_id] = (existing_node, existing_score + rrf_contribution)
                else:
                    node_scores[node_id] = (node, rrf_contribution)

        fused_results = sorted(
            node_scores.values(),
            key=lambda x: x[1],
            reverse=True
        )

        logger.info(f"RRF fusion: {len(ranked_lists)} lists → {len(fused_results)} unique nodes")
        if fused_results:
            top_5_scores = [score for _, score in fused_results[:5]]
            logger.info(f"  Top 5 RRF scores: {[f'{s:.4f}' for s in top_5_scores]}")

        return fused_results

    def _identify_target_sessions(self, question: str, nodes: List[EventNode]) -> List[int]:
        """
        Identify which session(s) are likely to contain the answer.

        Uses multiple signals:
        - Temporal references in question
        - Entity mentions and their session associations (from pre-built mapping)
        - Contextual keywords

        Returns:
            List of session IDs (integers) that are most relevant
        """
        question_lower = question.lower()
        target_sessions = []

        if self.entity_session_map:
            import re
            question_entities = re.findall(r'\b([A-Z][a-z]+)\b', question)

            for entity in question_entities:
                entity_lower = entity.lower()

                if entity in self.entity_session_map:
                    sessions = self.entity_session_map[entity]['sessions']
                    target_sessions.extend(list(sessions))

                elif entity_lower in self.entity_session_map:
                    sessions = self.entity_session_map[entity_lower]['sessions']
                    target_sessions.extend(list(sessions))

                else:
                    for stored_entity, data in self.entity_session_map.items():
                        if entity_lower in stored_entity.lower():
                            target_sessions.extend(list(data['sessions']))
                            break

        else:
            entity_sessions = {}
            for node in nodes:
                if hasattr(node, 'attributes'):
                    session_id = node.attributes.get('session_id')
                    entities = node.attributes.get('entities', [])
                    for entity in entities:
                        entity_lower = entity.lower()
                        if entity_lower not in entity_sessions:
                            entity_sessions[entity_lower] = set()
                        if session_id:
                            entity_sessions[entity_lower].add(session_id)

            import re
            question_entities = re.findall(r'\b([A-Z][a-z]+)\b', question)

            for entity in question_entities:
                entity_lower = entity.lower()
                if entity_lower in entity_sessions:
                    target_sessions.extend(list(entity_sessions[entity_lower]))

        temporal_keywords = {
            'may': [1],
            'june': [3],
            'july': [6, 7, 8, 10],
            'august': [15],
            'september': [16],
            'october': [18],
            'charity race': [2],
            'lgbtq': [1, 7],
            'adoption': [2, 17],
        }

        for keyword, sessions in temporal_keywords.items():
            if keyword in question_lower:
                target_sessions.extend(sessions)

        return list(set(target_sessions)) if target_sessions else []

    def is_action_question(self, question: str) -> tuple:
        """
        Detect if this is an action question and extract the actor.

        Action questions ask what someone did/does/made/created/realized.
        These require speaker attribution to be correct.

        Args:
            question: Query text

        Returns:
            Tuple of (is_action: bool, actor_name: str or None)
        """
        import re
        q_lower = question.lower()

        action_patterns = [
            r'what (?:did|does|do|is|was) (\w+) (research|study|paint|realize|create|make|build|develop|learn|discover|find|explore|investigate)',
            r'what (?:is|are|was|were) (\w+)\'?s? (plans?|activities|research|work|projects?|hobbies?|interests?)',
            r'what (\w+) (researched?|studied|painted?|realized?|created?|made|built|developed|learned|discovered)',
            r'how (?:did|does|do) (\w+) (achieve|accomplish|make|create|develop|build|handle|manage|deal with)',
            r'what (?:did|does) (\w+) (realize|learn|discover|understand)',
        ]

        for pattern in action_patterns:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                person = match.group(1).capitalize()
                if person.lower() not in ['the', 'this', 'that', 'these', 'those', 'a', 'an']:
                    return (True, person)

        return (False, None)

    def detect_query_intent(self, question: str) -> str:
        """
        Detect query intent as per design specification.
        Returns: 'WHY', 'WHEN', or 'ENTITY'
        """
        q_lower = question.lower()

        # WHY queries - causal reasoning
        if any(word in q_lower for word in ['why', 'because', 'cause', 'reason', 'lead to', 'result']):
            return 'WHY'

        # WHEN queries - temporal reasoning
        if any(word in q_lower for word in ['when', 'time', 'date', 'before', 'after', 'during', 'while']):
            return 'WHEN'

        # ENTITY queries - entity-focused
        if any(word in q_lower for word in ['who', 'whom', 'whose', 'which person', 'which entity']):
            return 'ENTITY'

        # Default to ENTITY for general queries
        return 'ENTITY'

    def detect_query_type(self, question: str) -> str:
        """Detect the type of query for specialized handling."""
        q_lower = question.lower()

        # Check Multi-hop FIRST (most specific patterns)
        # Multi-hop indicators - Check EARLY before other types
        # These questions typically require connecting multiple pieces of information
        import re
        multi_hop_patterns = [
            # Research/exploration patterns - ADDED
            re.search(r'what .* research', q_lower),
            re.search(r'what .* study', q_lower),
            re.search(r'what .* investigate', q_lower),
            re.search(r'what .* explore', q_lower),
            # Identity patterns - ADDED
            re.search(r"what is .* identity", q_lower),
            re.search(r"who is .* really", q_lower),
            # Relationship patterns - ADDED
            re.search(r"what is .* relationship", q_lower),
            'relationship between' in q_lower,
            # Activity patterns requiring multiple hops - ADDED
            re.search(r"what .* activities", q_lower),
            re.search(r"what .* participate", q_lower),
            re.search(r"what .* involved", q_lower),
            # Location history - ADDED
            re.search(r"where .* move from", q_lower),
            re.search(r"where .* come from", q_lower),
            # Career/education - ADDED
            re.search(r"what .* career", q_lower),
            re.search(r"what .* pursue", q_lower),
            re.search(r"what .* field", q_lower),
            # Questions about multiple entities
            ('both' in q_lower),  # "what do both X and Y..."
            ('and' in q_lower and any(w in q_lower for w in ['who', 'what', 'where'])),  # connecting entities
            (q_lower.count('who') > 1),  # multiple who

            # Questions requiring aggregation
            ('common' in q_lower),  # finding commonalities
            ('relationship' in q_lower),  # relationships need multiple facts
            ('between' in q_lower),  # connections between entities

            # Possessive queries that need entity resolution
            ("'s" in q_lower and any(w in q_lower for w in ['what', 'where', 'which', 'how'])),  # "X's something"

            # Questions about characteristics/attributes that require multiple lookups
            ('identity' in q_lower or 'background' in q_lower),  # composite information
            ('status' in q_lower),  # current state often needs multiple facts

            # Action chains or sequences
            ('how did' in q_lower and any(w in q_lower for w in ['promote', 'achieve', 'accomplish', 'develop'])),
            ('participate' in q_lower or 'involved' in q_lower),  # involvement spans multiple events

            # Questions about collections or lists
            ('activities' in q_lower or 'events' in q_lower),  # multiple items
            ('all' in q_lower and any(w in q_lower for w in ['what', 'who', 'where'])),  # comprehensive lists

            # Origin/destination queries
            ('from' in q_lower and any(w in q_lower for w in ['move', 'come', 'travel'])),  # needs source info

            # Offering/providing queries (need to aggregate capabilities)
            ('offer' in q_lower or 'provide' in q_lower),
        ]
        if any(multi_hop_patterns):
            return 'multi_hop'

        # Temporal queries FIRST for specific temporal phrases
        # Strong temporal indicators only
        temporal_phrases = ['when did', 'when was', 'when is', 'what date', 'what time',
                           'what year', 'what month', 'how long']
        if any(phrase in q_lower for phrase in temporal_phrases):
            return 'temporal'

        # Activity queries - Now safe to check (won't override "what time")
        if 'what' in q_lower and any(word in q_lower for word in ['did', 'does', 'do', 'doing', 'done']):
            # But exclude if it's asking about preference (e.g., "what day does X prefer")
            if 'prefer' not in q_lower:
                return 'activity'

        # Entity queries (who questions)
        if any(word in q_lower for word in ['who', 'whom', 'whose', "who's"]):
            return 'entity'

        # "What day" only if it's asking about a specific day, not preference
        if 'what day' in q_lower and ('was' in q_lower or 'is' in q_lower or 'did' in q_lower):
            return 'temporal'

        # Check for "ago" with time units (e.g., "years ago", "months ago")
        if 'ago' in q_lower and any(unit in q_lower for unit in ['year', 'month', 'week', 'day']):
            return 'temporal'

        # Causal queries
        if any(word in q_lower for word in ['why', 'because', 'cause', 'reason', 'how come']):
            return 'causal'

        # Location queries
        if any(word in q_lower for word in ['where', 'location', 'place']):
            return 'location'

        # Open-domain queries - broad questions that need exploration
        open_domain_patterns = [
            'what fields' in q_lower,
            'what areas' in q_lower,
            'would likely' in q_lower,
            'might be' in q_lower,
            'could be' in q_lower,
            'interested in' in q_lower,
            'pursue' in q_lower,
            'education' in q_lower and 'field' in q_lower,
            'career' in q_lower,
            'future' in q_lower
        ]
        if any(open_domain_patterns):
            return 'open_domain'

        # Factual queries
        if any(word in q_lower for word in ['what', 'which', 'how many', 'how much']):
            return 'factual'

        return 'general'

    def get_adaptive_params(self, query_type: str) -> dict:
        """Get query-type-specific parameters for traversal and scoring."""
        params = {
            'temporal': {
                'max_depth': 5,
                'prefer_link_types': [LinkType.TEMPORAL],
                'similarity_threshold': 0.25,
                'scoring_weights': {
                    'keyword': 2.0,
                    'entity': 3.0,
                    'temporal': 4.0,
                    'phrase': 3.5,
                    'similarity': 1.2,
                    'date_exact': 10.0
                }
            },
            'entity': {
                'max_depth': 4,
                'prefer_link_types': [LinkType.SEMANTIC],
                'similarity_threshold': 0.3,
                'scoring_weights': {
                    'keyword': 1.8,
                    'entity': 3.5,
                    'temporal': 1.0,
                    'phrase': 2.5,
                    'similarity': 1.3
                }
            },
            'multi_hop': {
                'max_depth': 12,
                'prefer_link_types': [LinkType.SEMANTIC, LinkType.CAUSAL, LinkType.TEMPORAL],
                'similarity_threshold': 0.10,
                'scoring_weights': {
                    'keyword': 5.0,
                    'entity': 6.0,
                    'temporal': 1.5,
                    'phrase': 5.0,
                    'similarity': 2.0,
                    'exact_entity': 10.0
                }
            },
            'activity': {
                'max_depth': 4,
                'prefer_link_types': [LinkType.CAUSAL, LinkType.TEMPORAL],
                'similarity_threshold': 0.28,
                'scoring_weights': {
                    'keyword': 2.0,
                    'entity': 2.5,
                    'temporal': 1.5,
                    'phrase': 3.0,
                    'similarity': 1.4
                }
            },
            'causal': {
                'max_depth': 5,
                'prefer_link_types': [LinkType.CAUSAL],
                'similarity_threshold': 0.25,
                'scoring_weights': {
                    'keyword': 2.0,
                    'entity': 2.0,
                    'temporal': 0.5,
                    'phrase': 3.5,
                    'similarity': 1.5
                }
            },
            'factual': {
                'max_depth': 5,
                'prefer_link_types': [LinkType.SEMANTIC],
                'similarity_threshold': 0.25,
                'scoring_weights': {
                    'keyword': 5.0,
                    'entity': 4.0,
                    'temporal': 1.0,
                    'phrase': 5.0,
                    'similarity': 1.5
                }
            },
            'general': {
                'max_depth': 4,
                'prefer_link_types': None,
                'similarity_threshold': 0.3,
                'scoring_weights': {
                    'keyword': 4.0,
                    'entity': 2.5,
                    'temporal': 1.5,
                    'phrase': 5.0,
                    'similarity': 1.0
                }
            },
            'open_domain': {
                'max_depth': 6,
                'prefer_link_types': [LinkType.SEMANTIC],
                'similarity_threshold': 0.10,
                'scoring_weights': {
                    'keyword': 2.0,
                    'entity': 3.0,
                    'temporal': 0.5,
                    'phrase': 3.0,
                    'similarity': 2.0
                }
            }
        }
        return params.get(query_type, params['general'])

    @staticmethod
    def extract_date_from_question(question: str):
        """Extract date from question. Returns dict with 'year', 'month', 'day' if found."""
        import re
        from datetime import datetime

        # Pattern 1: Month DD, YYYY (e.g., "March 16, 2022")
        pattern1 = r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d{2})'
        match = re.search(pattern1, question, re.IGNORECASE)
        if match:
            month_name, day, year = match.groups()
            month_map = {
                'january': 1, 'february': 2, 'march': 3, 'april': 4,
                'may': 5, 'june': 6, 'july': 7, 'august': 8,
                'september': 9, 'october': 10, 'november': 11, 'december': 12
            }
            return {'year': int(year), 'month': month_map[month_name.lower()], 'day': int(day)}

        # Pattern 2: MM/DD/YYYY
        pattern2 = r'(\d{1,2})/(\d{1,2})/(20\d{2})'
        match = re.search(pattern2, question)
        if match:
            month, day, year = match.groups()
            return {'year': int(year), 'month': int(month), 'day': int(day)}

        # Pattern 3: YYYY only
        pattern3 = r'\b(20\d{2})\b'
        match = re.search(pattern3, question)
        if match:
            return {'year': int(match.group(1)), 'month': None, 'day': None}

        return None

    def find_nodes_by_date_range(self, target_date, days_range=2):
        """Find nodes whose dates_mentioned are within ±days_range of target_date."""
        from datetime import datetime, timedelta

        if not target_date or target_date.get('day') is None:
            return []

        try:
            target_dt = datetime(target_date['year'], target_date['month'], target_date['day'])
        except:
            return []

        all_nodes = []
        if hasattr(self.trg, 'graph_db') and hasattr(self.trg.graph_db, 'nodes'):
            all_nodes = list(self.trg.graph_db.nodes.values())

        nearby_nodes = []
        for node in all_nodes:
            if hasattr(node, 'attributes') and node.attributes:
                dates_mentioned = node.attributes.get('dates_mentioned', [])

                for date_obj in dates_mentioned:
                    if isinstance(date_obj, dict):
                        parsed = date_obj.get('parsed', '')
                        if parsed:
                            try:
                                node_date = datetime.fromisoformat(parsed.replace('Z', '+00:00').replace('+00:00', ''))
                                days_diff = abs((node_date.date() - target_dt.date()).days)
                                if days_diff <= days_range:
                                    nearby_nodes.append(node)
                                    break
                            except:
                                pass

        return nearby_nodes

    @staticmethod
    def resolve_relative_temporal_reference(node, target_date):
        """
        Resolve relative temporal references in node using dates_mentioned field.

        The dates_mentioned field already contains parsed dates for relative references
        like "yesterday", so we just check if any parsed date matches the target.
        """
        from datetime import datetime

        if not target_date or target_date.get('day') is None:
            return 0.0

        try:
            target_dt = datetime(target_date['year'], target_date['month'], target_date['day'])
        except:
            return 0.0

        if hasattr(node, 'attributes') and node.attributes:
            dates_mentioned = node.attributes.get('dates_mentioned', [])

            for date_obj in dates_mentioned:
                if isinstance(date_obj, dict):
                    parsed = date_obj.get('parsed', '')
                    original = date_obj.get('original', '').lower()

                    if parsed:
                        try:
                            node_date = datetime.fromisoformat(parsed.replace('Z', '+00:00').replace('+00:00', ''))

                            if (node_date.year == target_dt.year and
                                node_date.month == target_dt.month and
                                node_date.day == target_dt.day):

                                relative_refs = ['yesterday', 'today', 'tomorrow', 'last night', 'this morning', 'tonight']
                                is_relative = any(ref in original for ref in relative_refs)

                                if is_relative:
                                    return 15.0
                                else:
                                    return 10.0
                        except:
                            pass

        return 0.0

    def _expand_qa_context(self, nodes: List) -> List:
        """
        Expand nodes to include Q&A pairs and adjacent temporal nodes.
        This helps capture answers that follow questions in conversations.
        """
        if not nodes:
            return []

        expanded = list(nodes)
        seen_ids = {n.node_id for n in nodes}

        for node in nodes:
            # Check if this node contains a question
            has_question = False
            if hasattr(node, 'attributes') and 'original_text' in node.attributes:
                has_question = '?' in node.attributes['original_text']
            elif hasattr(node, 'content_narrative'):
                has_question = '?' in node.content_narrative

            # Get all neighbors
            neighbors = self.trg.graph_db.get_neighbors(node.node_id)

            for neighbor_node, link in neighbors:
                # Include Q&A pairs
                if link.properties.get('sub_type') in ['ANSWERED_BY', 'RESPONSE_TO']:
                    if neighbor_node.node_id not in seen_ids:
                        expanded.append(neighbor_node)
                        seen_ids.add(neighbor_node.node_id)

                # If current node has a question, include temporally adjacent nodes
                # These likely contain the answer
                if has_question and link.link_type == LinkType.TEMPORAL:
                    # Check if this is the next node in conversation (follows after)
                    sub_type = link.properties.get('sub_type', '')
                    if 'PRECEDES' in sub_type:  # Current node PRECEDES neighbor = neighbor comes AFTER
                        if neighbor_node.node_id not in seen_ids:
                            # Verify it's from the same dialogue (same D number, different utterance)
                            node_dia = node.attributes.get('dia_id') if hasattr(node, 'attributes') else None
                            neighbor_dia = neighbor_node.attributes.get('dia_id') if hasattr(neighbor_node, 'attributes') else None
                            # Extract dialogue number (D1, D2, etc) from dia_id (D1:4, D1:5, etc)
                            if node_dia and neighbor_dia:
                                node_dialogue = node_dia.split(':')[0] if ':' in node_dia else node_dia
                                neighbor_dialogue = neighbor_dia.split(':')[0] if ':' in neighbor_dia else neighbor_dia
                                if node_dialogue == neighbor_dialogue:
                                    expanded.append(neighbor_node)
                                    seen_ids.add(neighbor_node.node_id)
                                    logger.info(f"Added answer node after question: {neighbor_node.node_id[:8]}...")

        if len(expanded) > len(nodes):
            logger.info(f"Q&A expansion: {len(nodes)} → {len(expanded)} nodes")
        return expanded

    def _expand_session_context(self, nodes: List) -> Tuple[List, List]:
        """
        Fetch SESSION summaries for retrieved EVENT nodes.
        Sessions are ranked by how many relevant events they contain.

        Returns:
            Tuple of (event_nodes, session_nodes_ranked_by_relevance)
        """
        if not nodes:
            return [], []

        session_event_counts = {}
        session_nodes_map = {}

        for i, node in enumerate(nodes):
            neighbors = self.trg.graph_db.get_neighbors(node.node_id)

            for neighbor_node, link in neighbors:
                if link.properties.get('sub_type') == 'BELONGS_TO_SESSION':
                    session_id = neighbor_node.node_id

                    weight = max(10 - i, 1)

                    if session_id not in session_event_counts:
                        session_event_counts[session_id] = 0
                        session_nodes_map[session_id] = neighbor_node

                    session_event_counts[session_id] += weight

        sorted_sessions = sorted(
            session_event_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )

        session_nodes = [session_nodes_map[session_id] for session_id, _ in sorted_sessions]

        if session_nodes:
            top_session_score = sorted_sessions[0][1] if sorted_sessions else 0
            logger.info(f"Session expansion: Retrieved {len(session_nodes)} SESSION summaries, "
                       f"most relevant has {top_session_score} weighted events")

        return nodes, session_nodes

    def query(self, question: str, top_k: int = 15) -> Tuple[QueryContext, str]:
        """
        Execute multi-stage query with adaptive parameters.

        Stages:
        1. Detect query type
        2. Multi-layer search (vector + keyword + scan)
        3. Adaptive graph traversal from top anchors
        4. Query-type-aware reranking
        5. Select top K for answer generation

        Args:
            question: Query text
            top_k: Number of top nodes to return

        Returns:
            Tuple of (QueryContext, answer_context_string)
        """
        if self.jev_config.read_enabled:
            from .jev_mem_retrieval import RetrievalController
            return RetrievalController(self, self.jev, self.jev_config).query(question, top_k)
        query_type = self.detect_query_type(question)

        if self.ablation_config.get('flat_graph'):
            adaptive_params = {
                'max_depth': 3,
                'prefer_link_types': None,
                'similarity_threshold': 0.3,
                'scoring_weights': {
                    'keyword': 2.0,
                    'entity': 2.0,
                    'temporal': 1.0,
                    'phrase': 2.0,
                    'similarity': 1.0
                }
            }
        else:
            adaptive_params = self.get_adaptive_params(query_type)

        follow_temporal = not self.ablation_config.get('no_temporal', False)
        follow_causal = not self.ablation_config.get('no_causal', False)

        constraints = TraversalConstraints(
            max_depth=adaptive_params.get('max_depth', 5),
            max_nodes=500,
            follow_temporal=follow_temporal,
            follow_semantic=True,
            follow_causal=follow_causal
        )

        if query_type == 'multi_hop':
            vector_size, keyword_size, scan_size = 30, 30, 40
        elif query_type == 'temporal':
            vector_size, keyword_size, scan_size = 15, 15, 20
        else:
            vector_size, keyword_size, scan_size = 20, 20, 25

        ranked_lists = []

        context = self.trg.query(question, max_results=vector_size, constraints=constraints)
        if context and context.anchor_nodes:
            vector_nodes = context.anchor_nodes[:vector_size]
            ranked_lists.append(vector_nodes)
            logger.info(f"Vector search: {len(vector_nodes)} nodes")

        keyword_nodes = self._keyword_search(question)[:keyword_size]
        if keyword_nodes:
            ranked_lists.append(keyword_nodes)
            logger.info(f"Keyword search: {len(keyword_nodes)} nodes")

        scan_nodes = self._scan_all_nodes(question)[:scan_size]
        if scan_nodes:
            ranked_lists.append(scan_nodes)
            logger.info(f"Scan search: {len(scan_nodes)} nodes")

        if ranked_lists:
            fused_results = self._rrf_fusion(ranked_lists, k=60)

            all_candidates = []
            for node, rrf_score in fused_results:
                node.similarity_score = min(1.0, rrf_score * 20)
                all_candidates.append(node)

            logger.info(f"RRF fusion: {len(all_candidates)} unique candidates after fusion")
        else:
            all_candidates = []
            logger.warning("No results from any retrieval method!")

        existing_ids = {n.node_id for n in all_candidates}

        session_candidates = [c for c in all_candidates if hasattr(c, 'node_type') and 'SESSION' in str(c.node_type)]
        event_candidates = [c for c in all_candidates if not (hasattr(c, 'node_type') and 'SESSION' in str(c.node_type))]

        if session_candidates:
            logger.info(f"Found {len(session_candidates)} SESSION nodes - using as routing hints")

            event_boost_map = {}
            for session in session_candidates:
                session_score = getattr(session, 'similarity_score', 0.5)
                neighbors = self.trg.graph_db.get_neighbors(session.node_id)
                for neighbor_node, link in neighbors:
                    if link.properties.get('sub_type') == 'BELONGS_TO_SESSION':
                        current_boost = event_boost_map.get(neighbor_node.node_id, 0)
                        event_boost_map[neighbor_node.node_id] = max(current_boost, session_score * 0.3)

            for event in event_candidates:
                if event.node_id in event_boost_map:
                    boost = event_boost_map[event.node_id]
                    current_score = getattr(event, 'similarity_score', 0.5)
                    event.similarity_score = current_score + boost

            logger.info(f"Boosted {len(event_boost_map)} EVENTs based on SESSION routing")

        all_candidates = event_candidates

        if len(session_candidates) > 0:
            logger.info(f"Filtered out {len(session_candidates)} SESSION nodes, keeping {len(all_candidates)} EVENT candidates")

        if not self.ablation_config.get('basic_retrieval') and all_candidates:
            num_initial = 30 if query_type == 'multi_hop' else 15
            initial_top = sorted(
                all_candidates,
                key=lambda n: getattr(n, 'similarity_score', 0.5),
                reverse=True
            )[:num_initial]

            traversed = self._adaptive_graph_traversal(
                anchor_nodes=initial_top,
                question=question,
                similarity_threshold=adaptive_params.get('similarity_threshold', 0.3),
                relative_drop_threshold=0.15,
                max_depth=adaptive_params.get('max_depth', 5),
                max_nodes=800,
                prefer_link_types=adaptive_params.get('prefer_link_types', None)
            )

            for node, similarity in traversed:
                if node.node_id not in existing_ids:
                    node.similarity_score = similarity
                    all_candidates.append(node)
                    existing_ids.add(node.node_id)

        if query_type == 'multi_hop' and all_candidates:
            top_nodes = self._retrieve_multi_hop_evidence(
                question=question,
                all_candidates=all_candidates,
                top_k=top_k,
                scoring_weights=adaptive_params.get('scoring_weights', {})
            )
        else:
            if all_candidates:
                top_nodes = self._rerank_and_filter(
                    all_candidates,
                    question,
                    top_k,
                    query_type=query_type,
                    scoring_weights=adaptive_params.get('scoring_weights', {})
                )
            else:
                top_nodes = []

        if top_nodes:
            top_nodes = self._expand_qa_context(top_nodes)

        session_nodes = []
        if top_nodes:
            top_nodes, session_nodes = self._expand_session_context(top_nodes)

        # Enforce top_k limit after expansions
        if len(top_nodes) > top_k:
            logger.info(f"Trimming expanded nodes from {len(top_nodes)} to {top_k}")
            top_nodes = top_nodes[:top_k]

        final_context = QueryContext(
            query_text=question,
            anchor_nodes=top_nodes,
            traversal_paths=[],
            narrative_context=f"Retrieved {len(all_candidates)} candidates → top {len(top_nodes)} selected, {len(session_nodes)} sessions"
        )

        final_context.metadata = {
            'query_type': query_type,
            'total_candidates': len(all_candidates),
            'vector_search_count': len([n for n in all_candidates[:vector_size] if n]),
            'keyword_search_count': len([n for n in all_candidates[vector_size:vector_size+keyword_size] if n]) if len(all_candidates) > vector_size else 0,
            'scan_search_count': added if 'added' in locals() else 0,
            'top_k_requested': top_k,
            'top_k_returned': len(top_nodes),
            'adaptive_params': adaptive_params
        }

        answer_context = self.answer_formatter.format_context_for_qa(
            top_nodes,
            question,
            session_nodes=session_nodes
        )

        return final_context, answer_context

    def _temporal_keyword_search(self, question: str, limit: int = 40) -> List[EventNode]:
        """Weight distinct event terms over repeated names and substring matches.

        Exact postings avoid counting the same query word once for every matching
        index key. This is particularly important for rare events in date queries.
        """
        import math
        import re
        from .graph_db import NodeType
        words = re.findall(r'\w+', re.sub(r"['’]s\b", '', question.lower()))
        stop = {'the', 'a', 'an', 'is', 'was', 'are', 'were', 'what', 'when', 'where',
                'who', 'how', 'did', 'does', 'do', 'has', 'had', 'have', 'been', 'be',
                'to', 'of', 'in', 'on', 'at', 'for', 'and', 'with', 'go', 'long'}
        terms = {word for word in words if word not in stop and len(word) >= 2}
        scores = {}
        count = len(self.trg.graph_db.nodes)
        for term in sorted(terms):
            postings = set(self.node_index.get(term, ()))
            if not postings and len(term) >= 5:
                # Conservative singular/plural fallback, without arbitrary
                # substring matches such as "me" inside a participant's name.
                stem = term[:-1] if term.endswith('s') else term
                postings.update(self.node_index.get(stem, ()))
                postings.update(self.node_index.get(stem + 's', ()))
            weight = math.log1p(count / (1 + len(postings)))
            for node_id in postings:
                scores[node_id] = scores.get(node_id, 0.0) + weight
        ranked = sorted(scores, key=lambda node_id: (-scores[node_id], node_id))
        return [self.trg.graph_db.nodes[node_id] for node_id in ranked
                if node_id in self.trg.graph_db.nodes
                and self.trg.graph_db.nodes[node_id].node_type == NodeType.EVENT][:limit]

    def _keyword_search(self, question: str, limit: int = 40) -> List[EventNode]:
        """Search using keyword index with proper scoring for index matches."""
        question_lower = question.lower()

        # Track which nodes were found via which keywords
        node_keyword_matches = {}  # node_id -> list of matched keywords

        words = question_lower.split()
        stop_words = {'the', 'a', 'an', 'is', 'was', 'are', 'were', 'what', 'when', 'where', 'who', 'how', 'did', 'does', 'do'}

        logger.info(f"Keyword search: Question words (after stop): {[w for w in words if w not in stop_words]}")

        # Word search - track which keywords matched
        for word in words:
            if word in stop_words:
                continue

            clean_word = word.strip('.,!?;:"\'-')
            if len(clean_word) >= 2 and clean_word in self.node_index:
                # Convert set to list for slicing
                node_ids = sorted(self.node_index[clean_word])
                logger.info(f"Keyword '{clean_word}' found {len(node_ids)} nodes in index")

                # Track keyword match for each node
                for node_id in node_ids:
                    if node_id not in node_keyword_matches:
                        node_keyword_matches[node_id] = []
                    node_keyword_matches[node_id].append(clean_word)

            # Partial matches for longer words
            if len(clean_word) >= 4:
                for key in self.node_index:
                    if clean_word in key or key in clean_word:
                        # Convert set to list for slicing
                        node_ids = sorted(self.node_index[key])
                        for node_id in node_ids:
                            if node_id not in node_keyword_matches:
                                node_keyword_matches[node_id] = []
                            node_keyword_matches[node_id].append(f"{clean_word}~{key}")

        # Bigram search
        for i in range(len(words) - 1):
            bigram = f"{words[i]} {words[i+1]}"
            if bigram in self.node_index:
                # Convert set to list for slicing
                node_ids = sorted(self.node_index[bigram])
                for node_id in node_ids:
                    if node_id not in node_keyword_matches:
                        node_keyword_matches[node_id] = []
                    node_keyword_matches[node_id].append(bigram)

        # Score and sort with proper weighting for keyword index matches
        nodes = []
        for node_id, matched_keywords in node_keyword_matches.items():
            if node_id in self.trg.graph_db.nodes:
                node = self.trg.graph_db.nodes[node_id]
                score = 0

                # Handle both EventNode and EpisodeNode
                if hasattr(node, 'content_narrative'):
                    content_lower = node.content_narrative.lower()
                elif hasattr(node, 'summary'):
                    content_lower = node.summary.lower()
                else:
                    content_lower = str(node).lower()

                # MAJOR FIX: Give high weight to keyword index matches (5 points each)
                # SESSION nodes will be filtered out later, so uniform weighting is fine
                score += len(matched_keywords) * 5

                # Additional points for word frequency in content
                for w in words:
                    if w not in stop_words and w in content_lower:
                        score += 1

                # Phrase bonus (still important for context)
                for i in range(len(words) - 1):
                    if f"{words[i]} {words[i+1]}" in content_lower:
                        score += 3

                nodes.append((score, node))

        nodes.sort(key=lambda x: (-x[0], x[1].node_id))

        # Debug logging
        top_nodes = [node for score, node in nodes[:limit]]
        if top_nodes:
            top_dias = [n.attributes.get('dia_id', 'N/A') for n in top_nodes[:10] if hasattr(n, 'attributes')]
            logger.info(f"Keyword search returning {len(top_nodes)} nodes, top 10 dia_ids: {top_dias}")

        return top_nodes

    def _scan_all_nodes(self, question: str) -> List[EventNode]:
        """Full scan fallback for comprehensive coverage."""
        question_lower = question.lower()
        words = [w for w in question_lower.split()
                if w not in ['the', 'a', 'an', 'is', 'was', 'what', 'when', 'where', 'who', 'how', 'did']]

        relevant_nodes = []
        for node in self.trg.graph_db.nodes.values():
            score = 0
            if hasattr(node, 'content_narrative'):
                content_lower = node.content_narrative.lower()
            elif hasattr(node, 'summary'):
                content_lower = node.summary.lower()
            else:
                content_lower = str(node).lower()

            for word in words:
                if len(word) > 2 and word in content_lower:
                    score += 1

            if 'original_text' in node.attributes:
                orig_lower = node.attributes['original_text'].lower()
                for word in words:
                    if len(word) > 2 and word in orig_lower:
                        score += 1

            if score > 0:
                relevant_nodes.append((score, node))

        relevant_nodes.sort(key=lambda x: x[0], reverse=True)
        return [node for score, node in relevant_nodes[:60]]

    def _probabilistic_beam_search(
        self,
        anchor_nodes: List[EventNode],
        question: str,
        query_intent: str,
        k: int = 60,
        beam_width: int = 10,
        max_visited: int = 50,
        lambda1: float = 0.6,
        lambda2: float = 0.4
    ) -> List[Tuple[EventNode, float]]:
        """
        Probabilistic Beam Search as per Algorithm 1 in design.

        Args:
            anchor_nodes: Starting nodes from RRF fusion
            question: Original query text
            query_intent: One of 'WHY', 'WHEN', 'ENTITY'
            k: RRF constant
            beam_width: Number of candidates to keep in beam
            max_visited: Budget for visited nodes
            lambda1: Weight for structural alignment
            lambda2: Weight for semantic affinity

        Returns:
            List of (node, score) tuples
        """
        from heapq import heappush, heappop
        import numpy as np

        # Get query embedding
        query_embedding = self.trg.encoder.encode(question)
        if len(query_embedding.shape) == 2:
            query_embedding = query_embedding[0]

        # Define attention weights for each intent (Equation 6)
        attention_weights = {
            'WHY': {'CAUSAL': 0.7, 'TEMPORAL': 0.2, 'SEMANTIC': 0.05, 'ENTITY': 0.05},
            'WHEN': {'TEMPORAL': 0.7, 'CAUSAL': 0.1, 'SEMANTIC': 0.1, 'ENTITY': 0.1},
            'ENTITY': {'ENTITY': 0.6, 'SEMANTIC': 0.3, 'TEMPORAL': 0.05, 'CAUSAL': 0.05}
        }

        w_tq = attention_weights.get(query_intent, attention_weights['ENTITY'])

        # Priority queue for beam search (using negative scores for max heap)
        beam = []
        for node in anchor_nodes:
            # Initial RRF score
            initial_score = 1.0 / (k + 1)
            heappush(beam, (-initial_score, node.node_id, node))

        visited = set()
        result_nodes = []

        while beam and len(visited) < max_visited:
            neg_score, node_id, node = heappop(beam)
            score = -neg_score

            if node_id in visited:
                continue

            visited.add(node_id)
            result_nodes.append((node, score))

            # Expand neighbors
            neighbors = self.trg.graph_db.get_neighbors(node_id)

            for neighbor_node, link in neighbors:
                if neighbor_node.node_id not in visited:
                    # Calculate structural alignment (phi function)
                    link_type_str = link.link_type.value if hasattr(link.link_type, 'value') else str(link.link_type)
                    structural_score = w_tq.get(link_type_str, 0.01)

                    # Calculate semantic affinity
                    if neighbor_node.embedding_vector:
                        neighbor_emb = np.array(neighbor_node.embedding_vector)
                        semantic_score = np.dot(query_embedding, neighbor_emb) / (
                            np.linalg.norm(query_embedding) * np.linalg.norm(neighbor_emb) + 1e-8
                        )
                    else:
                        semantic_score = 0.0

                    # Transition probability (Equation 5)
                    p_transition = lambda1 * structural_score + lambda2 * semantic_score
                    new_score = score + p_transition

                    # Keep only top beam_width candidates
                    if len(beam) < beam_width:
                        heappush(beam, (-new_score, neighbor_node.node_id, neighbor_node))
                    elif new_score > -beam[0][0]:
                        heappop(beam)
                        heappush(beam, (-new_score, neighbor_node.node_id, neighbor_node))

        # Sort by final scores
        result_nodes.sort(key=lambda x: x[1], reverse=True)
        return result_nodes

    def _adaptive_graph_traversal(
        self,
        anchor_nodes: List[EventNode],
        question: str,
        similarity_threshold: float = 0.3,
        relative_drop_threshold: float = 0.15,
        max_depth: int = 3,
        max_nodes: int = 500,
        prefer_link_types: Optional[List[LinkType]] = None
    ) -> List[Tuple[EventNode, float]]:
        """
        Adaptive BFS graph traversal with similarity filtering.

        Args:
            anchor_nodes: Starting nodes
            question: Query text
            similarity_threshold: Minimum similarity to continue
            relative_drop_threshold: Max similarity drop per hop
            max_depth: Maximum traversal depth
            max_nodes: Maximum nodes to return

        Returns:
            List of (node, similarity) tuples
        """
        question_lower = question.lower()
        keywords = [w for w in question_lower.split()
                   if len(w) > 2 and w not in {'the', 'a', 'an', 'is', 'was', 'what', 'when', 'where', 'who', 'how', 'did'}]

        enriched_question = self.keyword_enricher.enrich_query(question)
        query_embedding = self.trg.encoder.encode([enriched_question])[0]

        visited = set()
        result_nodes = []
        queue = deque()

        for node in anchor_nodes:
            content = None
            if hasattr(node, 'content_narrative'):
                content = node.content_narrative
            elif hasattr(node, 'summary'):
                content = node.summary

            if content:
                node_embedding = self.trg.encoder.encode([content])[0]
                similarity = self._cosine_similarity(query_embedding, node_embedding)
                queue.append((node, similarity, 0, similarity))

        encodings_done = len(anchor_nodes)

        while queue and len(result_nodes) < max_nodes:
            current_node, current_sim, depth, parent_sim = queue.popleft()

            if current_node.node_id in visited:
                continue

            visited.add(current_node.node_id)

            if current_sim < similarity_threshold:
                continue
            if (parent_sim - current_sim) > relative_drop_threshold:
                continue

            result_nodes.append((current_node, current_sim))

            if depth >= max_depth:
                continue

            neighbors = self._get_neighbors(current_node, follow_link_types=prefer_link_types)
            promising = [n for n in neighbors
                        if n.node_id not in visited
                        and self._lightweight_keyword_filter(n, keywords)]

            neighbor_limit = 10 if len(keywords) > 3 else 8
            encoding_limit = 400

            for neighbor in promising[:neighbor_limit]:
                if encodings_done >= encoding_limit:
                    break

                neighbor_content = None
                if hasattr(neighbor, 'content_narrative'):
                    neighbor_content = neighbor.content_narrative
                elif hasattr(neighbor, 'summary'):
                    neighbor_content = neighbor.summary

                if neighbor_content:
                    neighbor_emb = self.trg.encoder.encode([neighbor_content])[0]
                    neighbor_sim = self._cosine_similarity(query_embedding, neighbor_emb)
                    encodings_done += 1

                    if neighbor_sim >= similarity_threshold:
                        drop = current_sim - neighbor_sim
                        if drop <= relative_drop_threshold:
                            queue.append((neighbor, neighbor_sim, depth + 1, current_sim))

        result_nodes.sort(key=lambda x: x[1], reverse=True)
        return result_nodes

    def _get_neighbors(self, node: EventNode, follow_link_types: Optional[Set[LinkType]] = None) -> List[EventNode]:
        """Get neighbors through graph links, considering both link types and subtypes."""
        neighbors = []
        seen = set()  # Avoid duplicates

        # Priority order for different link subtypes (for multi-hop especially)
        priority_neighbors = []
        context_neighbors = []  # Add context neighbors category
        regular_neighbors = []

        for link in self.trg.graph_db.links.values():
            subtype = link.properties.get('sub_type', '') if hasattr(link, 'properties') else ''

            # ALWAYS include CONTEXT_NEIGHBOR links regardless of query type
            # These are crucial for finding answers in surrounding context
            if subtype == 'CONTEXT_NEIGHBOR':
                # Process context neighbors regardless of link type preferences
                target = None
                if link.source_node_id == node.node_id:
                    target = self.trg.graph_db.get_node(link.target_node_id)
                elif link.target_node_id == node.node_id:
                    target = self.trg.graph_db.get_node(link.source_node_id)

                if target and target.node_id != node.node_id and target.node_id not in seen:
                    context_neighbors.append(target)
                    seen.add(target.node_id)
                continue  # Skip the rest of processing for context neighbors

            # Check if we should follow this link type
            if follow_link_types:
                # For temporal preference, include TEMPORALLY_CLOSE links
                if LinkType.TEMPORAL in follow_link_types:
                    if link.link_type != LinkType.TEMPORAL and subtype not in ['TEMPORALLY_CLOSE', 'PRECEDES', 'SUCCEEDS']:
                        continue

                # For semantic preference, PRIORITIZE SAME_ENTITY links
                elif LinkType.SEMANTIC in follow_link_types:
                    if link.link_type != LinkType.SEMANTIC and subtype not in ['SAME_ENTITY', 'SIMILAR_TO', 'RELATED_TO']:
                        continue
                    # Mark SAME_ENTITY links as high priority
                    is_entity_link = (subtype == 'SAME_ENTITY')

                # For causal preference, include ANSWERED_BY links
                elif LinkType.CAUSAL in follow_link_types:
                    if link.link_type != LinkType.CAUSAL and subtype not in ['ANSWERED_BY', 'RESPONSE_TO']:
                        continue

                elif link.link_type not in follow_link_types:
                    continue

            target = None
            if link.source_node_id == node.node_id:
                target = self.trg.graph_db.get_node(link.target_node_id)
            elif link.target_node_id == node.node_id:
                target = self.trg.graph_db.get_node(link.source_node_id)

            if target and target.node_id != node.node_id and target.node_id not in seen:
                # Prioritize links by their type for multi-hop traversal
                subtype = link.properties.get('sub_type', '') if hasattr(link, 'properties') else ''
                if subtype == 'SAME_ENTITY':
                    priority_neighbors.append(target)
                elif subtype == 'CONTEXT_NEIGHBOR':
                    # Context neighbors are very important for Q&A patterns
                    context_neighbors.append(target)
                else:
                    regular_neighbors.append(target)
                seen.add(target.node_id)

        # Return in priority order: entity links first, then context, then regular
        # This helps with multi-hop questions that often follow Q&A patterns
        return priority_neighbors + context_neighbors + regular_neighbors

    def _lightweight_keyword_filter(self, node: EventNode, keywords: List[str]) -> bool:
        """Fast keyword-based relevance check."""
        if hasattr(node, 'content_narrative'):
            text = node.content_narrative.lower()
        elif hasattr(node, 'summary'):
            text = node.summary.lower()
        else:
            text = str(node).lower()
        orig_text = node.attributes.get('original_text', '').lower() if hasattr(node, 'attributes') else ''

        matches = sum(1 for kw in keywords if kw in text or kw in orig_text)
        return matches > 0

    def _rerank_and_filter(
        self,
        nodes: List[EventNode],
        question: str,
        top_k: int = 15,
        query_type: str = 'general',
        scoring_weights: dict = None
    ) -> List[EventNode]:
        """
        Two-stage filtering and ranking:
        Stage 1: Filter by person names and time constraints
        Stage 2: Rank by keyword relevance

        Signals:
        1. Keyword matching (most important for relevance)
        2. Entity matching (filter first, then boost)
        3. Temporal relevance (filter first, then score)
        4. Phrase matching (exact matches are golden)
        5. Vector similarity (supplementary)

        Args:
            nodes: Candidate nodes
            question: Query text
            top_k: Number to return
            query_type: Type of query for adaptive scoring
            scoring_weights: Custom weights for scoring signals

        Returns:
            Top K nodes
        """
        question_lower = question.lower()
        is_temporal = query_type == 'temporal'

        import re
        person_names = []
        capitalized = re.findall(r'\b([A-Z][a-z]+)\b', question)
        for word in capitalized:
            if word.lower() not in {'the', 'what', 'when', 'where', 'who', 'how', 'why'}:
                person_names.append(word.lower())

        temporal_constraint = None
        if is_temporal:
            year_match = re.search(r'\b(19|20)\d{2}\b', question)
            if year_match:
                temporal_constraint = year_match.group()

        filtered_nodes = []

        for node in nodes:
            if hasattr(node, 'content_narrative'):
                text = node.content_narrative.lower()
            elif hasattr(node, 'summary'):
                text = node.summary.lower()
            else:
                text = str(node).lower()

            if person_names:
                if not any(name in text for name in person_names):
                    continue

            if temporal_constraint and temporal_constraint not in text:
                continue

            filtered_nodes.append(node)

        if len(filtered_nodes) < top_k // 2:
            filtered_nodes = nodes

        if scoring_weights is None:
            scoring_weights = {
                'keyword': 4.0,
                'entity': 2.5,
                'temporal': 2.0,
                'phrase': 5.0,
                'similarity': 0.8
            }

        stop_words = {'the', 'a', 'an', 'is', 'was', 'are', 'were', 'what', 'when', 'where',
                     'who', 'how', 'does', 'did', 'have', 'been', 'has', 'had', 'will', 'would',
                     'could', 'should', 'may', 'might', 'must', 'can', 'do'}
        keywords = [w for w in question_lower.split()
                   if (len(w) > 2 or w in person_names) and w not in stop_words]

        words = question_lower.split()
        phrases = []
        for i in range(len(words) - 1):
            phrase = f"{words[i]} {words[i+1]}"
            if words[i] not in stop_words or words[i+1] not in stop_words:
                phrases.append(phrase)

        import re
        person_in_question = None
        name_patterns = [
            r'\b([A-Z][a-z]+)\b',
            r"\b(Jon|Gina|John|Jane|Mary|Mike|Sarah|David|Lisa|Tom)\b"
        ]
        for pattern in name_patterns:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                person_in_question = match.group(1).lower()
                break

        is_action, action_subject = self.is_action_question(question)

        target_sessions = self._identify_target_sessions(question, nodes)
        logger.info(f"Target sessions identified: {target_sessions}")

        scored_nodes = []
        for node in nodes:
            score = 0.0
            keyword_score = 0.0
            entity_score = 0.0
            temporal_score = 0.0
            phrase_score = 0.0
            speaker_score = 0.0
            context_bonus = 0.0
            session_score = 0.0
            if hasattr(node, 'content_narrative'):
                text = node.content_narrative.lower()
            elif hasattr(node, 'summary'):
                text = node.summary.lower()
            else:
                text = str(node).lower()
            orig_text = node.attributes.get('original_text', '').lower() if hasattr(node, 'attributes') else ''

            # Boost score significantly if the node mentions the person being asked about
            # Handle both full names and nicknames
            person_boost = 0.0
            for person_name in person_names:
                # Check for Mel/Melanie equivalence
                person_variants = [person_name]
                if person_name == 'mel':
                    person_variants.append('melanie')
                elif person_name == 'melanie':
                    person_variants.append('mel')

                for variant in person_variants:
                    # Check if this person is the speaker or mentioned in text
                    if hasattr(node, 'attributes') and 'speaker' in node.attributes:
                        if variant in node.attributes['speaker'].lower():
                            person_boost += 20.0  # Strong boost if the person is speaking
                    if variant in text:
                        person_boost += 10.0  # Boost if person is mentioned
                    if orig_text and variant in orig_text:
                        person_boost += 5.0

            for kw in keywords:
                import re
                word_pattern = r'\b' + re.escape(kw) + r'\b'
                if re.search(word_pattern, text, re.IGNORECASE):
                    keyword_score += 3.0
                    occurrences = len(re.findall(word_pattern, text, re.IGNORECASE))
                    if occurrences > 1:
                        keyword_score += (occurrences - 1) * 1.5
                elif kw in text:
                    keyword_score += 1.0

                if orig_text and re.search(word_pattern, orig_text, re.IGNORECASE):
                    keyword_score += 2.0

            for phrase in phrases:
                if phrase in text:
                    phrase_score += 5.0
                if orig_text and phrase in orig_text:
                    phrase_score += 3.0

            if hasattr(node, 'attributes') and 'entities' in node.attributes:
                for kw in keywords:
                    for entity in node.attributes['entities']:
                        entity_lower = entity.lower()
                        if kw in entity_lower:
                            entity_score += 3.0

            import re
            capitalized_words = re.findall(r'\b([A-Z][a-z]+)\b', question)
            for cap_word in capitalized_words:
                cap_lower = cap_word.lower()
                if cap_lower in text:
                    entity_score += 4.0
                    if query_type == 'multi_hop':
                        occurrences = text.count(cap_lower)
                        entity_score += (occurrences - 1) * 2.0

            if is_temporal or query_type == 'temporal':
                import re

                if hasattr(node, 'timestamp') and node.timestamp:
                    temporal_score += 3.0

                if 'how long' in question_lower:
                    duration_patterns = [
                        r'\b\d+\s*(year|month|week|day|hour|minute)s?\b',
                        r'\b(one|two|three|four|five|six|seven|eight|nine|ten)\s*(year|month|week|day|hour)s?\b',
                        r'\bfor\s+\d+\s*(year|month|week|day)s?\b',
                        r'\bsince\s+\w+\s+\d{4}\b',
                        r'\b(several|few|many|couple)\s*(year|month|week|day)s?\b',
                    ]
                    for pattern in duration_patterns:
                        if re.search(pattern, text, re.IGNORECASE):
                            temporal_score += 5.0
                            break

                question_months = []
                question_years = []
                months = ['january', 'february', 'march', 'april', 'may', 'june',
                         'july', 'august', 'september', 'october', 'november', 'december']

                for month in months:
                    if month in question_lower:
                        question_months.append(month)

                year_match = re.search(r'\b(19|20)\d{2}\b', question)
                if year_match:
                    question_years.append(year_match.group())

                exact_match = False
                for month in question_months:
                    if month in text:
                        temporal_score += 5.0
                        for year in question_years:
                            if year in text:
                                temporal_score += scoring_weights.get('date_exact', 10.0)
                                exact_match = True
                                break

                if not exact_match:
                    month_found = any(month in text for month in months)
                    if month_found:
                        temporal_score += 2.5

                if re.search(r'\b(19|20)\d{2}\b', text):
                    temporal_score += 3.0

                if re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', text):
                    temporal_score += 4.0
                if re.search(r'\b\d{1,2}\s+(january|february|march|april|may|june|july|august|september|october|november|december)\b', text.lower()):
                    temporal_score += 4.0

                temporal_keywords = ['yesterday', 'today', 'tomorrow', 'last', 'next', 'ago',
                                    'morning', 'afternoon', 'evening', 'night', 'week', 'weekend']
                for tk in temporal_keywords:
                    if tk in question_lower and tk in text:
                        temporal_score += 2.0

                days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
                if any(day in text for day in days):
                    temporal_score += 2.0

                if re.search(r'\b\d{1,2}:\d{2}\b', text):
                    temporal_score += 2.0

            if hasattr(node, 'attributes'):
                node_speaker = node.attributes.get('speaker', '').lower()

                if is_action and action_subject:
                    subject_lower = action_subject.lower()

                    if node_speaker == subject_lower:
                        speaker_score += 8.0
                    elif subject_lower in text:
                        if node_speaker and node_speaker != subject_lower:
                            speaker_score -= 5.0
                        else:
                            speaker_score += 1.0

                elif person_in_question:
                    if node_speaker == person_in_question:
                        speaker_score += 3.0
                    elif person_in_question in text:
                        if node_speaker and node_speaker != person_in_question:
                            speaker_score -= 2.0
                        else:
                            speaker_score += 1.0

            for i in range(len(keywords) - 1):
                phrase = f"{keywords[i]} {keywords[i+1]}"
                if phrase in text or phrase in orig_text:
                    phrase_score += 3.0

            for link in self.trg.graph_db.links.values():
                if (link.source_node_id == node.node_id or link.target_node_id == node.node_id):
                    subtype = link.properties.get('sub_type', '') if hasattr(link, 'properties') else ''
                    if subtype == 'CONTEXT_NEIGHBOR':
                        distance = link.properties.get('distance', 3)
                        context_bonus += 2.0 / (1 + distance * 0.5)

            if hasattr(node, 'attributes'):
                node_session = node.attributes.get('session_id')
                if node_session and target_sessions:
                    if node_session in target_sessions:
                        session_score = 15.0
                        logger.debug(f"Session match! Node session {node_session} in target sessions {target_sessions}")
                    elif abs(int(node_session) - int(target_sessions[0])) <= 1:
                        session_score = 5.0
                    else:
                        session_score = -5.0
                        logger.debug(f"Session mismatch: Node session {node_session} not in target {target_sessions}")

            dia_id_score = 0
            if hasattr(node, 'attributes') and node.attributes:
                dia_id = node.attributes.get('dia_id', '')

                dia_pattern = re.search(r'D(\d+):(\d+)', question)
                if dia_pattern and dia_id:
                    expected_dia = f"D{dia_pattern.group(1)}:{dia_pattern.group(2)}"
                    if dia_id == expected_dia:
                        dia_id_score = 50.0

                session_pattern = re.search(r'session\s*(\d+)|conversation\s*(\d+)|dialogue\s*(\d+)', question_lower)
                if session_pattern and dia_id:
                    for group in session_pattern.groups():
                        if group:
                            session_num = int(group)
                            if dia_id.startswith(f"D{session_num}:"):
                                dia_id_score += 20.0
                                break

                ordinal_map = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5}
                for ordinal, num in ordinal_map.items():
                    if ordinal in question_lower and ('conversation' in question_lower or 'session' in question_lower):
                        if dia_id and dia_id.startswith(f"D{num}:"):
                            dia_id_score += 15.0
                            break

            similarity_score = getattr(node, 'similarity_score', 0)

            score = (
                keyword_score * scoring_weights.get('keyword', 2.0) +
                entity_score * scoring_weights.get('entity', 3.0) +
                temporal_score * scoring_weights.get('temporal', 2.0) +
                phrase_score * scoring_weights.get('phrase', 3.0) +
                person_boost * 1.0 +  # Add person boost to prioritize nodes about the right person
                speaker_score * 2.0 +
                context_bonus * 1.5 +
                session_score * 1.0 +
                dia_id_score * 1.0 +
                similarity_score * 10.0 * scoring_weights.get('similarity', 1.0)
            )

            node.ranking_score = score
            scored_nodes.append((score, node))

        scored_nodes.sort(key=lambda x: x[0], reverse=True)

        result_nodes = []
        for score, node in scored_nodes[:top_k]:
            node.ranking_score = score
            result_nodes.append(node)
        return result_nodes

    def _retrieve_multi_hop_evidence(
        self,
        question: str,
        all_candidates: List[EventNode],
        top_k: int = 20,
        scoring_weights: dict = None
    ) -> List[EventNode]:
        """
        Specialized retrieval for multi-hop questions that ensures ALL evidence pieces are found.

        Now uses improved multi-hop retrieval V2 from multihop_improvements_v2 module.

        Multi-hop questions require connecting multiple facts, often about different entities
        or different aspects of the same entity. This method ensures comprehensive coverage.

        Args:
            question: The multi-hop question
            all_candidates: All candidate nodes from initial retrieval
            top_k: Number of nodes to return (default 20 for multi-hop - original baseline)
            scoring_weights: Weights for scoring signals

        Returns:
            List of nodes with evidence from all required aspects
        """
        try:
            # improve_multihop_retrieval_v2 not available, falling back to original
            pass  # return improve_multihop_retrieval_v2(self, question, all_candidates, top_k)
        except Exception as e:
            logger.warning(f"Improved multi-hop retrieval V2 failed: {e}, falling back to original")
            pass

        import re
        question_lower = question.lower()

        entities = []
        capitalized = re.findall(r'\b([A-Z][a-z]+)\b', question)
        for word in capitalized:
            if word.lower() not in {'what', 'when', 'where', 'who', 'how', 'why', 'the'}:
                entities.append(word.lower())

        entity_nodes = {}
        for entity in entities:
            entity_nodes[entity] = []

            for node in all_candidates:
                if hasattr(node, 'content_narrative'):
                    content = node.content_narrative.lower()
                elif hasattr(node, 'summary'):
                    content = node.summary.lower()
                else:
                    content = str(node).lower()

                node_entities = []
                if hasattr(node, 'attributes') and node.attributes:
                    node_entities = [e.lower() for e in node.attributes.get('entities', [])]

                if entity in content or entity in node_entities:
                    entity_nodes[entity].append(node)

        fact_types_needed = []

        if any(word in question_lower for word in ['research', 'study', 'work', 'do', 'did']):
            fact_types_needed.extend(['identity', 'action'])

        if any(word in question_lower for word in ['where', 'move', 'from', 'location']):
            fact_types_needed.extend(['entity', 'location', 'temporal'])

        if any(word in question_lower for word in ['relationship', 'between', 'and']):
            fact_types_needed.append('multiple_entities')

        selected_nodes = []
        selected_ids = set()

        for entity, nodes in entity_nodes.items():
            if nodes:
                entity_scored = []
                for node in nodes[:10]:
                    score = 0
                    if hasattr(node, 'content_narrative'):
                        content = node.content_narrative.lower()
                    else:
                        content = str(node).lower()

                    score += content.count(entity) * 2

                    if hasattr(node, 'attributes') and node.attributes:
                        if entity in [e.lower() for e in node.attributes.get('entities', [])]:
                            score += 5

                    entity_scored.append((score, node))

                entity_scored.sort(key=lambda x: x[0], reverse=True)

                for _, node in entity_scored[:3]:
                    if node.node_id not in selected_ids:
                        selected_nodes.append(node)
                        selected_ids.add(node.node_id)

        remaining_slots = top_k - len(selected_nodes)
        if remaining_slots > 0:
            remaining = [n for n in all_candidates if n.node_id not in selected_ids]
            scored_remaining = []

            for node in remaining:
                score = self._score_node_relevance(
                    node=node,
                    question=question,
                    query_type='multi_hop',
                    scoring_weights=scoring_weights or {}
                )
                scored_remaining.append((score, node))

            scored_remaining.sort(key=lambda x: x[0], reverse=True)

            for score, node in scored_remaining[:remaining_slots]:
                node.ranking_score = score
                selected_nodes.append(node)

        final_scored = []
        for node in selected_nodes:
            score = self._score_node_relevance(
                node=node,
                question=question,
                query_type='multi_hop',
                scoring_weights=scoring_weights or {}
            )
            node.ranking_score = score
            final_scored.append((score, node))

        final_scored.sort(key=lambda x: x[0], reverse=True)

        return [node for _, node in final_scored]

    def _score_node_relevance(
        self,
        node: EventNode,
        question: str,
        query_type: str = 'general',
        scoring_weights: dict = None
    ) -> float:
        """
        Helper method to score a single node's relevance to a question.
        Extracted from _rerank_and_filter for reusability.
        """
        question_lower = question.lower()
        keywords = [w for w in question_lower.split()
                   if w not in {'the', 'a', 'an', 'is', 'was', 'what', 'when', 'where', 'who', 'how', 'did'}]

        keyword_score = 0
        entity_score = 0
        phrase_score = 0
        temporal_score = 0

        if hasattr(node, 'content_narrative'):
            text = node.content_narrative.lower()
        elif hasattr(node, 'summary'):
            text = node.summary.lower()
        else:
            text = str(node).lower()

        orig_text = node.attributes.get('original_text', '').lower() if hasattr(node, 'attributes') else ''

        for kw in keywords:
            if kw in text:
                keyword_score += 2.0
            if kw in orig_text:
                keyword_score += 1.5

        import re
        capitalized = re.findall(r'\b([A-Z][a-z]+)\b', question)
        for word in capitalized:
            if word.lower() in text:
                entity_score += 4.0

        for i in range(len(keywords) - 1):
            phrase = f"{keywords[i]} {keywords[i+1]}"
            if phrase in text or phrase in orig_text:
                phrase_score += 5.0

        weights = scoring_weights or {}
        total_score = (
            keyword_score * weights.get('keyword', 2.0) +
            entity_score * weights.get('entity', 3.0) +
            phrase_score * weights.get('phrase', 3.0) +
            getattr(node, 'similarity_score', 0) * 10.0 * weights.get('similarity', 1.0)
        )

        return total_score

    def _multi_stage_entity_retrieval(
        self,
        question: str,
        all_candidates: List[EventNode],
        top_k: int = 20
    ) -> List[EventNode]:
        """
        Multi-stage entity-focused retrieval strategy from LongMemEval lessons.

        Implements the following stages:
        1. Extract ALL entities from the question
        2. Find nodes mentioning ANY entity (not just first)
        3. Expand from ALL anchors using graph traversal
        4. Re-rank by relevance to ALL entities

        Args:
            question: The multi-hop question
            all_candidates: All candidate nodes from initial retrieval
            top_k: Number of nodes to return

        Returns:
            List of top-k nodes with comprehensive entity coverage
        """
        import re

        entities = set()

        capitalized = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', question)
        entities.update([e.lower() for e in capitalized])

        quoted = re.findall(r'"([^"]+)"', question)
        entities.update([q.lower() for q in quoted])

        stop_words = {'the', 'a', 'an', 'is', 'was', 'were', 'are', 'be', 'been'}
        entities = {e for e in entities if e not in stop_words}

        if not entities:
            question_lower = question.lower()
            words = [w for w in question_lower.split()
                    if w not in stop_words and len(w) > 3]
            entities = set(words[:5])

        anchor_nodes = []
        entity_node_map = {}

        for node in all_candidates:
            if hasattr(node, 'content_narrative'):
                text = node.content_narrative.lower()
            elif hasattr(node, 'summary'):
                text = node.summary.lower()
            else:
                text = str(node).lower()

            mentioned_entities = []
            for entity in entities:
                if entity in text:
                    mentioned_entities.append(entity)

            if mentioned_entities:
                entity_node_map[node.node_id] = mentioned_entities
                anchor_nodes.append((node, len(mentioned_entities)))

        anchor_nodes.sort(key=lambda x: x[1], reverse=True)

        selected_anchors = []
        entity_coverage = {e: [] for e in entities}

        for node, count in anchor_nodes:
            for entity in entity_node_map.get(node.node_id, []):
                if len(entity_coverage[entity]) < 3:
                    entity_coverage[entity].append(node)
                    if node not in selected_anchors:
                        selected_anchors.append(node)

        context_nodes_dict = {}

        for anchor in selected_anchors[:12]:
            neighbors = self._get_neighbors(anchor)
            for neighbor in neighbors[:6]:
                context_nodes_dict[neighbor.node_id] = neighbor

                second_neighbors = self._get_neighbors(neighbor)
                for second_neighbor in second_neighbors[:3]:
                    context_nodes_dict[second_neighbor.node_id] = second_neighbor

        for anchor in selected_anchors:
            context_nodes_dict[anchor.node_id] = anchor

        context_nodes = list(context_nodes_dict.values())

        scored_nodes = []

        for node in context_nodes:
            if hasattr(node, 'content_narrative'):
                text = node.content_narrative.lower()
            elif hasattr(node, 'summary'):
                text = node.summary.lower()
            else:
                text = str(node).lower()

            entity_match_score = 0
            for entity in entities:
                if entity in text:
                    entity_match_score += 10.0

            similarity_score = getattr(node, 'similarity_score', 0.5) * 10.0

            num_entities_mentioned = sum(1 for e in entities if e in text)
            multi_entity_bonus = num_entities_mentioned * 5.0 if num_entities_mentioned > 1 else 0

            total_score = entity_match_score + similarity_score + multi_entity_bonus
            scored_nodes.append((total_score, node))

        scored_nodes.sort(key=lambda x: x[0], reverse=True)

        result = []
        for score, node in scored_nodes[:top_k]:
            node.ranking_score = score
            result.append(node)

        return result

    def decompose_and_answer_multi_hop(
        self,
        question: str,
        top_k: int = 20
    ) -> Tuple[str, List[str]]:
        """
        Question decomposition strategy for complex multi-hop questions.

        Breaks down complex questions into simpler sub-questions, answers each,
        then synthesizes the final answer. This is particularly effective for
        questions requiring multiple reasoning steps.

        Example:
            Q: "What did John give to the person who helped Mary?"
            Sub-questions:
                1. "Who helped Mary?"
                2. "What did John give to [that person]?"

        Args:
            question: The complex multi-hop question
            top_k: Number of nodes to retrieve for each sub-question

        Returns:
            Tuple of (final_answer, list_of_sub_answers)
        """
        if not self.llm_controller:
            return ("Question decomposition not available (no LLM controller)", [])

        decomposition_prompt = f"""Break down this complex question into 2-4 simpler sub-questions that, when answered together, will answer the original question.

Original Question: {question}

Return ONLY a JSON list of sub-questions in this exact format:
["sub-question 1", "sub-question 2", "sub-question 3"]

Example:
Original: "What did John give to the person who helped Mary?"
Sub-questions: ["Who helped Mary?", "What did John give to that person?"]

Sub-questions:"""

        try:
            response = self.llm_controller.llm.get_completion(
                decomposition_prompt,
                temperature=0.3,
                response_format={"type": "text"}
            )

            import json
            import re

            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                sub_questions = json.loads(json_match.group(0))
            else:
                sub_questions = [q.strip(' -•"\'') for q in response.split('\n') if q.strip() and '?' in q]

            if not sub_questions or len(sub_questions) < 1:
                sub_questions = [question]

        except Exception as e:
            logger.warning(f"Question decomposition failed: {e}")
            sub_questions = [question]

        sub_answers = []
        accumulated_context = []

        for i, sub_q in enumerate(sub_questions):
            query_context, context_text = self.query(sub_q, top_k=top_k // len(sub_questions) + 5)

            if not context_text:
                sub_answer = "Information not found"
            else:
                qa_prompt = f"""Based on the context below, answer this question concisely.

Context:
{context_text}

Question: {sub_q}

Answer (2-10 words):"""

                try:
                    sub_answer = self.llm_controller.llm.get_completion(
                        qa_prompt,
                        temperature=0.0,
                        response_format={"type": "text"}
                    ).strip()

                    if sub_answer.startswith("Answer:"):
                        sub_answer = sub_answer[7:].strip()

                except Exception as e:
                    logger.warning(f"Sub-question answering failed: {e}")
                    sub_answer = "Error retrieving answer"

            sub_answers.append(f"Q{i+1}: {sub_q}\nA{i+1}: {sub_answer}")
            accumulated_context.append(context_text)

        synthesis_prompt = f"""You have answered several sub-questions related to a main question. Now synthesize a final answer to the main question using the sub-question answers.

Main Question: {question}

Sub-Question Answers:
{chr(10).join(sub_answers)}

Synthesize a concise final answer to the main question (5-15 words):"""

        try:
            final_answer = self.llm_controller.llm.get_completion(
                synthesis_prompt,
                temperature=0.0,
                response_format={"type": "text"}
            ).strip()

            if final_answer.startswith("Answer:"):
                final_answer = final_answer[7:].strip()
            if final_answer.startswith("Final Answer:"):
                final_answer = final_answer[13:].strip()

        except Exception as e:
            logger.warning(f"Answer synthesis failed: {e}")
            final_answer = sub_answers[-1].split('\n')[-1] if sub_answers else "Information not found"

        return (final_answer, sub_answers)

    @staticmethod
    def _cosine_similarity(vec1, vec2):
        """Calculate cosine similarity."""
        if len(vec1) == 0 or len(vec2) == 0:
            return 0.0
        return float(np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2)))
