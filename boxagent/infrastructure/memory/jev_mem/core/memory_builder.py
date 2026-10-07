"""
Memory Builder Module

Handles memory construction from conversation data, including:
- Event extraction from turns
- Episode-based segmentation
- Link creation (temporal, semantic, causal)
- Memory indexing
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict
from tqdm import tqdm

from .trg_memory import TemporalResonanceGraphMemory, Link, LinkType
from .graph_db import SessionNode, NodeType, LinkSubType
from .episode_segmenter import EpisodeSegmenter, Episode
from .temporal_parser import TemporalParser

logger = logging.getLogger(__name__)

class MemoryBuilder:
    """
    Builds and manages Jev-Mem conversational memory.

    Supports both turn-based and episode-based memory construction.
    """

    def __init__(
        self,
        cache_dir: str,
        llm_model: str = "gpt-4o-mini",
        use_episodes: bool = False,
        embedding_model: str = "minilm",
        jev_config=None,
        jev_client=None,
        trg_memory=None,
        llm_enabled: bool = True,
    ):
        """
        Initialize memory builder.

        Args:
            cache_dir: Directory for caching memory
            llm_model: LLM model name (e.g., "gpt-4o-mini", "gpt-4o")
            use_episodes: Whether to use episode-based segmentation
        """
        import os
        from boxagent.infrastructure.memory.jev_mem.utils.memory_layer import LLMController
        from .answer_formatter import AnswerFormatter

        self.cache_dir = Path(cache_dir)
        self.llm_model = llm_model
        self.use_episodes = use_episodes
        self.embedding_model = embedding_model

        from .jev_mem_config import JevMemConfig
        from .jev_client import JevClient
        from .jev_mem_policies import WritePolicy
        self.jev_config = jev_config or JevMemConfig()
        self.jev = jev_client or JevClient(self.jev_config)
        self.write_policy = WritePolicy(self.jev, self.jev_config)
        self._jev_writes = 0
        self._consolidating = False
        if use_episodes and self.jev_config.write_enabled:
            raise ValueError("Jev-Mem writes use one canonical node per observation; disable --use-episodes")

        self.trg = trg_memory if trg_memory is not None else TemporalResonanceGraphMemory(
            llm_backend='openai' if llm_enabled else None,
            llm_model=llm_model,
            enable_async=False,
            embedding_model=embedding_model
        )
        if trg_memory is None:
            # Load graph and vectors together only in load(). Passing persist_dir
            # to TRG here auto-loads just vectors, contaminating --rebuild runs.
            self.trg.persist_dir = self.cache_dir
            self.trg.vector_db.persist_path = str(self.cache_dir / "vectors")

        api_key = os.getenv('OPENAI_API_KEY')
        self.llm_controller = None
        if api_key and llm_enabled:
            self.llm_controller = LLMController(
                backend='openai',
                model=llm_model,
                api_key=api_key
            )
        elif llm_enabled:
            logger.warning(
                "\n" + "="*60 +
                "\nWARNING: OPENAI_API_KEY not set!" +
                "\n- Using simple extraction fallback (limited accuracy)" +
                "\n- Set OPENAI_API_KEY environment variable for better results" +
                "\n" + "="*60
            )

        self.temporal_parser = TemporalParser()
        self.answer_formatter = AnswerFormatter()

        self.episode_segmenter = None
        if use_episodes and self.llm_controller:
            self.episode_segmenter = EpisodeSegmenter(
                llm_controller=self.llm_controller,
                max_buffer_size=5,
                min_episode_size=1
            )

        self.entities = {}
        self.node_index = {}
        self.episode_nodes = []
        self.episode_event_map = {}
        self.session_nodes = {}
        self.session_event_map = {}

    def build(self, interaction, timestamp=None, metadata=None):
        """Explicit Jev-Mem write path; returns the canonical EventNode or None.

        Optional admission -> typing -> bounded candidates -> relations -> insertion.
        No System-Two extraction is used for successful System-One writes.
        """
        from collections import Counter
        from dataclasses import asdict
        import numpy as np
        from .graph_db import EventNode
        from .jev_mem_policies import find_candidates

        if not isinstance(interaction, str) or not interaction.strip():
            raise ValueError("interaction must be non-empty text")
        metadata = dict(metadata or {})
        if not self.jev_config.write_enabled:
            return self._build_magma(interaction, timestamp, metadata)
        admission_data = score = None
        explicit = bool(metadata.get("explicit"))
        if self.jev_config.admission_enabled and not explicit:
            duplicate = any(n.attributes.get("raw_content") == interaction for n in self.trg.graph_db.nodes.values())
            from itertools import islice
            from .jev_mem_policies import node_state
            recent = [node_state(self.trg.graph_db.get_node(key)) for key in
                      islice(reversed(self.trg.graph_db.nodes), self.jev_config.candidate_top_k)
                      if self.trg.graph_db.get_node(key).node_type == NodeType.EVENT]
            assessment = self.write_policy.assess_observation(interaction, duplicate, recent)
            if assessment is None:
                return self._build_magma(interaction, timestamp, metadata, fallback=True)
            admission, memory_type = assessment
            admission_data = asdict(admission)
            score = admission.score(self.jev_config.admission_weights)
            if score < self.jev_config.admission_threshold:
                self.jev.audit.emit("memory_rejected", admission=admission_data, admission_score=score)
                return None
        else:
            memory_type = self.write_policy.memory_type(interaction)
            if memory_type is None:
                return self._build_magma(interaction, timestamp, metadata, fallback=True)
            if explicit:
                admission_data = {
                    "should_store": 1.0, "future_utility": 1.0,
                    "importance": 1.0, "novelty": 1.0, "redundancy": 0.0,
                }
                score = 1.0
        entities = metadata.get("entities", self._simple_entity_extraction(interaction))
        if not isinstance(entities, list) or any(not isinstance(e, str) for e in entities):
            raise ValueError("entities must be a list of strings")
        keywords = self.trg.keyword_enricher.extract_keywords(interaction)
        node = EventNode(timestamp=timestamp, content_narrative=interaction, attributes={
            **metadata, "raw_content": interaction, "original_text": metadata.get("original_text", interaction),
            "entities": sorted(set(entities)), "keywords": keywords,
            "jev_mem": {"admission_enabled": self.jev_config.admission_enabled,
                        "admission": admission_data, "admission_score": score,
                        "memory_type": asdict(memory_type), "controller": "mock" if self.jev_config.jev_mock else self.jev_config.decision_backend}})
        node.attributes["temporal_references"] = self.temporal_parser.describe_references(interaction, timestamp)
        enriched = self.trg.keyword_enricher.enrich_content(interaction, metadata=node.attributes)
        embedding = np.asarray(self.trg.encoder.encode(enriched)).reshape(-1)
        node.embedding_vector = embedding.tolist()
        candidates = find_candidates(self.trg, node, self.jev_config.candidate_top_k)
        relations = self.write_policy.relations(node, candidates)
        if relations is None:
            return self._build_magma(interaction, timestamp, metadata, fallback=True)
        temporal_count = self._store_jev_node(node, relations)
        self.index_event(node.node_id, interaction, node.attributes)
        self._jev_writes += 1
        relation_counts = Counter(link.link_type.value.lower() for link in relations)
        relation_counts["temporal"] += temporal_count
        self.jev.audit.emit("memory_constructed", memory_id=node.node_id, admission=admission_data,
                            admission_enabled=self.jev_config.admission_enabled,
                            memory_type=asdict(memory_type), admission_score=score,
                            temporal_controller="magma", relations_created=dict(relation_counts))
        interval = self.jev_config.consolidation_interval
        if interval and not self._consolidating and self._jev_writes % interval == 0:
            self.consolidate(node.node_id)
        return node

    def _store_jev_node(self, node, relations):
        """Commit a single node and its views; undo partial insertion on failure."""
        import numpy as np
        for link in relations:
            other_id = link.target_node_id if link.source_node_id == node.node_id else link.source_node_id
            if not self.trg.graph_db.get_node(other_id):
                raise ValueError("Relation candidate disappeared before insertion")
        if not self.trg.vector_db.add_vector(node.node_id, np.asarray(node.embedding_vector), metadata={
                "entities": node.attributes.get("entities", []), "keywords": node.attributes.get("keywords", [])}):
            raise ValueError("Vector insertion failed")
        try:
            self.trg.graph_db.add_node(node)
            for link in relations:
                self.trg.graph_db.add_link(link)
            # Reuse MAGMA's LoCoMo sequence/proximity rules incrementally.
            # These neighbors are independent of Jev's bounded candidate set.
            peers = [other.node_id for other in self.trg.graph_db.nodes.values()
                     if other.node_type == NodeType.EVENT
                     and (not node.attributes.get("dia_id") or
                          (other.attributes.get("dia_id") and
                           other.attributes.get("source") == node.attributes.get("source")))]
            temporal_count = self.create_temporal_links(peers, latest_only=True)
            temporal_count += self.create_temporal_proximity_links(peers, latest_only=True)
        except Exception:
            self.trg.graph_db.delete_node(node.node_id)
            self.trg.vector_db.delete_vector(node.node_id)
            raise
        self.trg.stats['events_added'] += 1
        self.trg.stats['links_created'] += len(relations) + temporal_count
        return temporal_count

    def _build_magma(self, interaction, timestamp, metadata, fallback=False):
        if fallback:
            metadata["jev_mem"] = {"controller": "magma_fallback"}
            self.jev.audit.emit("write_fallback", controller="magma")
        node_id = self.trg.add_event(interaction, timestamp=timestamp, metadata=metadata)
        self.index_event(node_id, interaction, metadata)
        return self.trg.graph_db.get_node(node_id)

    def consolidate(self, memory_id, summarizer=None):
        """Periodic, non-destructive consolidation; raw evidence always survives.

        A supplied System-Two summarizer receives source texts only after Jev
        approves a merge/promotion. Without it, decisions and links are persisted.
        """
        from .jev_mem_policies import find_candidates, node_state
        import hashlib
        node = self.trg.graph_db.get_node(memory_id)
        if node is None:
            raise ValueError("Unknown memory_id")
        candidates = find_candidates(self.trg, node, self.jev_config.candidate_top_k)
        if not candidates:
            return []
        from typesafe_sdk import Choice
        from .jev_questions import consolidation_questions, choice_fixture
        questions = {f"pair_{i}_{name}": question for i in range(len(candidates))
                     for name, question in consolidation_questions(i).items()}
        mock = {key: choice_fixture(question, "keep_separate") if isinstance(question, Choice) else 0.0
                for key, question in questions.items()}
        result = self.jev.evaluate("consolidation", {"new_memory": node_state(node),
                                   "candidates": [node_state(n) for n in candidates]}, questions, mock_values=mock)
        if result is None:
            return []
        decisions = []
        for i, other in enumerate(candidates):
            scores = {name: result.values[f"pair_{i}_{name}"] for name in ("redundant", "contradiction", "obsolete", "link")}
            representation = result.choices[f"pair_{i}_representation"]
            decisions.append({"candidate_id": other.node_id, **scores, "representation": representation.model_dump()})
            threshold = self.jev_config.consolidation_threshold
            subtype = "CONTRADICTS" if scores["contradiction"] >= threshold else (
                "REDUNDANT_WITH" if scores["redundant"] >= threshold else "RELATED_TO")
            if max(scores["link"], scores["redundant"], scores["contradiction"]) >= threshold:
                link_id = hashlib.sha256((node.node_id + other.node_id + subtype).encode()).hexdigest()
                if not self.trg.graph_db.get_link(link_id):
                    self.trg.graph_db.add_link(Link(link_id=link_id, source_node_id=node.node_id,
                        target_node_id=other.node_id, link_type=LinkType.SEMANTIC,
                        properties={"sub_type": subtype, "probability": max(scores["link"], scores["redundant"], scores["contradiction"])},
                        metadata={"controller": "jev-mem", "origin": result.source}))
                    self.trg.stats['links_created'] += 1
            if (summarizer and representation.choice in ("merge", "promote")
                    and representation.probabilities[representation.choice] >= threshold
                    and scores["contradiction"] < threshold):
                summary_key = hashlib.sha256((node.node_id + other.node_id).encode()).hexdigest()
                already_done = any(n.attributes.get("consolidation_key") == summary_key for n in self.trg.graph_db.nodes.values())
                if not already_done:
                    text = summarizer([node.content_narrative, other.content_narrative])
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("System-Two summarizer returned empty text")
                    # Reuse admission and typing for the new representation, suppress periodic recursion.
                    self._consolidating = True
                    try:
                        summary = self.build(text, metadata={"source": "jev_mem_consolidation",
                            "parent_interaction_id": node.node_id, "consolidation_key": summary_key,
                            "source_memory_ids": [node.node_id, other.node_id],
                            "consolidation_action": representation.choice})
                    finally:
                        self._consolidating = False
                    self.jev.audit.emit("consolidation_summary", memory_id=summary.node_id if summary else None, llm_calls=1)
        attrs = dict(node.attributes)
        attrs["jev_mem"] = {**attrs.get("jev_mem", {}), "consolidation": decisions}
        self.trg.graph_db.update_node(node.node_id, {"attributes": attrs})
        self.jev.audit.emit("consolidation", memory_id=node.node_id, decisions=decisions)
        return decisions

    def _simple_entity_extraction(self, text: str) -> List[str]:
        """
        Simple fallback entity extraction using regex and heuristics.
        Used when LLM extraction fails or for image captions.
        """
        import re
        entities = []

        name_pattern = r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b'
        potential_names = re.findall(name_pattern, text)

        common_words = {'The', 'This', 'That', 'These', 'Those', 'What', 'When',
                       'Where', 'Who', 'Why', 'How', 'Image', 'Thanks', 'Wow',
                       'Yes', 'No', 'Maybe', 'Please', 'Sorry', 'Hello', 'Hi',
                       'Good', 'Great', 'Nice', 'Sure', 'Okay', 'Well', 'Now'}

        for name in potential_names:
            if name not in common_words and len(name) > 2:
                entities.append(name)

        im_pattern = r"I'?m\s+([A-Z][a-z]+)"
        im_matches = re.findall(im_pattern, text)
        entities.extend(im_matches)

        seen = set()
        unique_entities = []
        for entity in entities:
            if entity not in seen:
                seen.add(entity)
                unique_entities.append(entity)

        return unique_entities[:5]

    def extract_event(self, turn, session_id: str, timestamp: datetime, prev_turn=None, next_turn=None) -> Dict:
        """
        Extract event node from a single conversation turn.

        Args:
            turn: Conversation turn object
            session_id: Session identifier
            timestamp: Session timestamp

        Returns:
            Event data dictionary
        """
        content_parts = [f"[{turn.speaker}]: {turn.text}"]

        entities = []
        topic = "general"
        dates_mentioned = []

        if self.llm_controller and hasattr(self.llm_controller, 'llm'):
            context_info = ""
            if prev_turn:
                context_info += f"\nPrevious: [{prev_turn.speaker}]: {prev_turn.text[:100]}..."
            if next_turn:
                context_info += f"\nNext: [{next_turn.speaker}]: {next_turn.text[:100]}..."

            extraction_prompt = f"""
            Extract key information from this conversational turn:

            Speaker: {turn.speaker}
            Text: {turn.text}{context_info}

            Return ONLY a valid JSON object with:
            {{
                "entities": ["list of people, places, things mentioned"],
                "topic": "brief topic/theme",
                "dates_mentioned": ["any dates/times mentioned"],
                "summary": "1-sentence summary",
                "semantic_facts": ["key facts or statements that could answer future questions"],
                "relationships": ["any relationships mentioned (e.g., 'X researches Y', 'A is B's friend')"],
                "activities": ["specific activities or actions mentioned"],
                "context_keywords": ["important keywords from surrounding context that relate to this turn"]
            }}

            Focus on extracting facts that might be needed for multi-hop reasoning.
            Pay special attention to Q&A patterns where this turn might be answering a previous question.
            Ensure the response is valid JSON only, no additional text.
            """

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = self.llm_controller.llm.get_completion(
                        extraction_prompt,
                        response_format={"type": "text"},
                        temperature=0.1 if attempt > 0 else 0.0
                    )

                    response = response.strip()
                    if response.startswith("```json"):
                        response = response[7:]
                    if response.startswith("```"):
                        response = response[3:]
                    if response.endswith("```"):
                        response = response[:-3]
                    response = response.strip()

                    extracted = json.loads(response)
                    entities = extracted.get('entities', [])
                    topic = extracted.get('topic', 'general')
                    dates_mentioned = extracted.get('dates_mentioned', [])

                    if 'summary' in extracted:
                        content_parts.insert(0, f"Summary: {extracted['summary']}")

                    semantic_facts = extracted.get('semantic_facts', [])
                    relationships = extracted.get('relationships', [])
                    activities = extracted.get('activities', [])
                    context_keywords = extracted.get('context_keywords', [])

                    if semantic_facts:
                        content_parts.append(f"Facts: {'; '.join(semantic_facts)}")
                    if relationships:
                        content_parts.append(f"Relationships: {'; '.join(relationships)}")
                    if activities:
                        content_parts.append(f"Activities: {'; '.join(activities)}")
                    if context_keywords:
                        content_parts.append(f"Context: {'; '.join(context_keywords)}")

                    break

                except json.JSONDecodeError as e:
                    if attempt < max_retries - 1:
                        logger.debug(f"JSON parse error on attempt {attempt + 1}: {e}. Retrying...")
                        if "[Image:" in turn.text and attempt == 1:
                            extraction_prompt = f"""
                            Extract entities and topic from: "{turn.text[:200]}"

                            Return ONLY this JSON:
                            {{"entities": ["names of people/places"], "topic": "main topic", "dates_mentioned": [], "summary": "brief summary"}}
                            """
                        continue
                    else:
                        logger.warning(f"Failed to extract event info after {max_retries} attempts: {e}")
                        entities = self._simple_entity_extraction(turn.text)
                        topic = "conversation" if "[Image:" in turn.text else "general"
                except Exception as e:
                    if "rate_limit" in str(e).lower() or "429" in str(e):
                        logger.error(f"Rate limit hit: {e}")
                        logger.info("Using fallback extraction due to rate limit")
                        entities = self._simple_entity_extraction(turn.text)
                        topic = "general"
                        dates_mentioned = []
                        break
                    elif attempt < max_retries - 1:
                        logger.debug(f"Extraction error on attempt {attempt + 1}: {e}. Retrying...")
                        continue
                    else:
                        logger.warning(f"Failed to extract event info after {max_retries} attempts: {e}")
                        entities = self._simple_entity_extraction(turn.text)
                        topic = "general"
                        dates_mentioned = []

        for entity in entities:
            if entity not in self.entities:
                self.entities[entity] = {
                    'first_seen': timestamp,
                    'mentions': []
                }
            self.entities[entity]['mentions'].append({
                'session': session_id,
                'timestamp': timestamp
            })

        parsed_dates = []
        for date_str in dates_mentioned:
            parsed = self.temporal_parser.extract_temporal_reference(date_str, timestamp)
            if parsed:
                parsed_dates.append({
                    'original': date_str,
                    'parsed': parsed.isoformat() if isinstance(parsed, datetime) else str(parsed)
                })

        try:
            if ':' in str(turn.dia_id):
                turn_number = int(str(turn.dia_id).split(':')[1])
            else:
                turn_number = int(turn.dia_id) if turn.dia_id else 0
        except (ValueError, IndexError):
            turn_number = 0

        actual_timestamp = timestamp + timedelta(hours=turn_number)

        metadata = {
            'speaker': turn.speaker,
            'entities': entities,
            'topic': topic,
            'dates_mentioned': parsed_dates,
            'session_id': session_id,
            'dia_id': turn.dia_id,
            'original_text': turn.text
        }

        if 'semantic_facts' in locals() and semantic_facts:
            metadata['semantic_facts'] = semantic_facts
        if 'relationships' in locals() and relationships:
            metadata['relationships'] = relationships
        if 'activities' in locals() and activities:
            metadata['activities'] = activities

        return {
            'content': '\n'.join(content_parts),
            'metadata': metadata,
            'timestamp': actual_timestamp
        }

    def create_episode_node(self, episode: Episode, session_id: str, event_ids: List[str]) -> str:
        """
        Create an Episode node in the graph.

        Args:
            episode: Episode object
            session_id: Session identifier
            event_ids: List of event node IDs in this episode

        Returns:
            Episode node ID
        """
        from .graph_db import EpisodeNode
        from .vector_db import VectorEncoder

        episode_node = EpisodeNode(
            node_id=episode.episode_id,
            title=episode.title,
            summary=episode.content,
            start_timestamp=episode.start_timestamp,
            end_timestamp=episode.end_timestamp,
            event_count=episode.message_count,
            boundary_reason=episode.boundary_reason,
            event_node_ids=event_ids,
            attributes={
                'session_id': session_id,
                'participants': episode.participants,
                'metadata': episode.metadata
            }
        )

        if self.embedding_model == 'openai':
            encoder = VectorEncoder(model_name='text-embedding-3-small', use_openai=True)
        else:
            encoder = VectorEncoder(model_name='all-MiniLM-L6-v2', use_openai=False)
        embeddings = encoder.encode(f"{episode.title} {episode.content}")
        episode_node.embedding_vector = embeddings[0] if len(embeddings.shape) > 1 else embeddings

        self.trg.graph_db.add_node(episode_node)

        self.trg.vector_db.add_vector(
            vector_id=episode_node.node_id,
            vector=episode_node.embedding_vector
        )

        self.episode_nodes.append(episode_node.node_id)
        self.episode_event_map[episode_node.node_id] = event_ids

        return episode_node.node_id

    def create_session_nodes(self, sample):
        """
        Create SESSION nodes from session summaries.

        Args:
            sample: LoCoMoSample object with session_summary data
        """
        from .vector_db import VectorEncoder

        session_summaries = sample.session_summary

        if self.embedding_model == 'openai':
            encoder = VectorEncoder(model_name='text-embedding-3-small', use_openai=True)
        else:
            encoder = VectorEncoder(model_name='all-MiniLM-L6-v2', use_openai=False)

        for session_id in sorted(sample.conversation.sessions.keys()):
            session = sample.conversation.sessions[session_id]
            summary_key = f"session_{session_id}_summary"
            summary_text = session_summaries.get(summary_key, "")

            if not summary_text:
                logger.warning(f"No summary found for session {session_id}")
                continue

            session_node = SessionNode(
                session_id=session_id,
                summary=summary_text,
                date_time=session.date_time,
                attributes={
                    'num_turns': len(session.turns),
                    'speakers': {sample.conversation.speaker_a, sample.conversation.speaker_b}
                }
            )

            embeddings = encoder.encode(summary_text)
            session_node.embedding_vector = embeddings[0] if len(embeddings.shape) > 1 else embeddings

            self.trg.graph_db.add_node(session_node)

            self.trg.vector_db.add_vector(
                vector_id=session_node.node_id,
                vector=session_node.embedding_vector
            )

            self.session_nodes[session_id] = session_node.node_id
            self.session_event_map[session_id] = []

            logger.info(f"Created SESSION node for session {session_id}: {session_node.node_id[:8]}")

    def create_event_from_episode(self, episode: Episode, session_id: str) -> Dict:
        """
        Convert an episode into an event node.

        Args:
            episode: Episode object
            session_id: Session identifier

        Returns:
            Event dictionary
        """
        content_parts = [
            f"Episode: {episode.title}",
            f"Summary: {episode.content}",
            "Original conversation:"
        ]

        for msg in episode.original_messages:
            speaker = msg.get('speaker', 'Unknown')
            text = msg.get('text', '')
            content_parts.append(f"  {speaker}: {text}")

        event_content = '\n'.join(content_parts)

        all_entities = []
        all_topics = []
        all_original_texts = []

        for msg in episode.original_messages:
            if 'entities' in msg:
                all_entities.extend(msg['entities'])
            if 'topic' in msg:
                all_topics.append(msg['topic'])
            if 'text' in msg:
                all_original_texts.append(msg['text'])

        unique_entities = list(set(all_entities))

        for entity in unique_entities:
            if entity not in self.entities:
                self.entities[entity] = {
                    'first_seen': episode.start_timestamp or datetime.now(),
                    'mentions': []
                }
            self.entities[entity]['mentions'].append({
                'session': session_id,
                'episode_id': episode.episode_id,
                'timestamp': episode.start_timestamp
            })

        timestamp = episode.start_timestamp or datetime.now()
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)

        return {
            'content': event_content,
            'metadata': {
                'episode_id': episode.episode_id,
                'episode_title': episode.title,
                'session_id': session_id,
                'message_count': episode.message_count,
                'boundary_reason': episode.boundary_reason,
                'entities': unique_entities,
                'topics': list(set(all_topics)) if all_topics else [],
                'participants': episode.participants,
                'original_text': ' '.join(all_original_texts),
                'is_episode': True
            },
            'timestamp': timestamp
        }

    def _index_text_basic(self, event_id: str, text: str):
        """
        Index a text string for keyword search (helper method).
        Indexes both single words and bigrams.
        """
        if not text:
            return

        text = text.lower()
        words = text.split()

        for word in words:
            word = word.strip('.,!?;:"')
            if len(word) >= 2:
                if word not in self.node_index:
                    self.node_index[word] = set()
                self.node_index[word].add(event_id)

        for i in range(len(words) - 1):
            bigram = f"{words[i]} {words[i + 1]}"
            bigram = bigram.strip('.,!?;:"')
            if bigram not in self.node_index:
                self.node_index[bigram] = set()
            self.node_index[bigram].add(event_id)

    def index_event(self, event_id: str, text: str, metadata: dict = None):
        """
        Build keyword index with semantic enrichment for fast search.

        Indexes both:
        1. Original text (for exact phrase matches)
        2. Semantic extractions (for topic/action/relationship matches)

        Args:
            event_id: Event node ID
            text: Original text to index
            metadata: Event metadata containing semantic extractions
        """
        if text:
            self._index_text_basic(event_id, text)

        if metadata:
            for rel in metadata.get('relationships', []):
                if rel:
                    self._index_text_basic(event_id, rel)

            for activity in metadata.get('activities', []):
                if activity:
                    self._index_text_basic(event_id, activity)

            for fact in metadata.get('semantic_facts', []):
                if fact:
                    self._index_text_basic(event_id, fact)

            for keyword in metadata.get('context_keywords', []):
                if keyword:
                    self._index_text_basic(event_id, keyword)

    def create_temporal_links(self, nodes: List[str], *, latest_only: bool = False) -> int:
        """MAGMA sequence links; optionally append only the final observation."""
        created = 0

        start = max(0, len(nodes) - 2) if latest_only else 0
        for i in range(start, len(nodes) - 1):
            link = Link(
                source_node_id=nodes[i],
                target_node_id=nodes[i + 1],
                link_type=LinkType.TEMPORAL,
                properties={
                    'sub_type': 'PRECEDES',
                    'sequence_index': i
                }
            )
            self.trg.graph_db.add_link(link)
            created += 1

            reverse_link = Link(
                source_node_id=nodes[i + 1],
                target_node_id=nodes[i],
                link_type=LinkType.TEMPORAL,
                properties={
                    'sub_type': 'SUCCEEDS',
                    'sequence_index': i
                }
            )
            self.trg.graph_db.add_link(reverse_link)
            created += 1

        return created

    def create_context_links(self, nodes: List[str], window_size: int = 3) -> int:
        """
        Create context links between nearby nodes in conversation.
        This helps capture Q&A patterns where answer follows question.

        Args:
            nodes: List of node IDs in temporal order
            window_size: Number of nodes before/after to link

        Returns:
            Number of links created
        """
        created = 0

        for i, node_id in enumerate(nodes):
            node = self.trg.graph_db.get_node(node_id)
            if not node:
                continue

            # Link to nodes within the context window
            start_idx = max(0, i - window_size)
            end_idx = min(len(nodes), i + window_size + 1)

            for j in range(start_idx, end_idx):
                if i == j:
                    continue

                target_id = nodes[j]
                target_node = self.trg.graph_db.get_node(target_id)
                if not target_node:
                    continue

                # Calculate distance-based weight (closer = stronger)
                distance = abs(i - j)
                weight = 1.0 / (1 + distance * 0.5)  # Decay factor

                # Create bidirectional context link
                link = Link(
                    source_node_id=node_id,
                    target_node_id=target_id,
                    link_type=LinkType.SEMANTIC,
                    properties={
                        'sub_type': 'CONTEXT_NEIGHBOR',
                        'distance': distance,
                        'weight': weight,
                        'direction': 'forward' if j > i else 'backward'
                    }
                )
                self.trg.graph_db.add_link(link)
                created += 1

        return created

    def create_semantic_links(self, nodes: List[str], top_k: int = 3) -> int:
        """Create semantic links between nodes."""
        created = 0
        from .vector_db import VectorEncoder
        import numpy as np

        encoder = VectorEncoder()

        for node_id in nodes:
            node = self.trg.graph_db.get_node(node_id)
            if not node:
                continue

            if hasattr(node, 'embedding_vector') and node.embedding_vector is not None:
                embedding = node.embedding_vector
                if isinstance(embedding, list):
                    embedding = np.array(embedding, dtype=np.float32)
            else:
                node_text = str(node.content_narrative) if hasattr(node, 'content_narrative') else str(node)
                embeddings = encoder.encode(node_text)
                embedding = embeddings[0] if len(embeddings.shape) > 1 else embeddings

            similar_ids = self.trg.vector_db.search(
                embedding,
                k=top_k + 1
            )

            for sim_id, similarity, _ in similar_ids:
                if sim_id != node_id and similarity > 0.5:
                    target_node = self.trg.graph_db.get_node(sim_id)
                    if target_node:
                        link = Link(
                            source_node_id=node_id,
                            target_node_id=sim_id,
                            link_type=LinkType.SEMANTIC,
                            properties={
                                'sub_type': 'SIMILAR_TO',
                                'similarity': float(similarity)
                            }
                        )
                        self.trg.graph_db.add_link(link)
                        created += 1
                    else:
                        logger.debug(f"Skipping semantic link: target node {sim_id} not found in graph")

        return created

    def create_causal_links(self, nodes: List[str]) -> int:
        """Create causal RESPONSE_TO links."""
        created = 0

        if self.use_episodes:
            # Episode mode: link between episodes with different participants
            for i in range(len(nodes) - 1):
                curr_node = self.trg.graph_db.get_node(nodes[i])
                next_node = self.trg.graph_db.get_node(nodes[i + 1])

                if curr_node and next_node:
                    curr_participants = curr_node.attributes.get('participants', [])
                    next_participants = next_node.attributes.get('participants', [])

                    # Create causal link if different participants
                    if curr_participants != next_participants:
                        link = Link(
                            source_node_id=nodes[i],
                            target_node_id=nodes[i + 1],
                            link_type=LinkType.CAUSAL,
                            properties={
                                'sub_type': 'RESPONSE_TO',
                                'confidence': 0.8
                            }
                        )
                        self.trg.graph_db.add_link(link)
                        created += 1
        else:
            # Turn mode: link between different speakers
            for i in range(len(nodes) - 1):
                curr = self.trg.graph_db.get_node(nodes[i])
                next_node = self.trg.graph_db.get_node(nodes[i + 1])

                if curr and next_node:
                    curr_speaker = curr.attributes.get('speaker') if hasattr(curr, 'attributes') else None
                    next_speaker = next_node.attributes.get('speaker') if hasattr(next_node, 'attributes') else None

                    if curr_speaker and next_speaker and curr_speaker != next_speaker:
                        link = Link(
                            source_node_id=nodes[i],
                            target_node_id=nodes[i + 1],
                            link_type=LinkType.CAUSAL,
                            properties={
                                'sub_type': 'RESPONSE_TO',
                                'confidence': 0.8
                            }
                        )
                        self.trg.graph_db.add_link(link)
                        created += 1

        return created

    def create_entity_links(self, nodes: List[str]) -> int:
        """Create links between nodes mentioning same entities (for multi-hop)."""
        from collections import defaultdict
        entity_index = defaultdict(list)
        created = 0

        for node_id in nodes:
            node = self.trg.graph_db.get_node(node_id)
            if node and hasattr(node, 'attributes') and 'entities' in node.attributes:
                for entity in node.attributes['entities']:
                    entity_normalized = entity.lower().strip()
                    if entity_normalized:
                        entity_index[entity_normalized].append(node_id)

        for entity, node_list in entity_index.items():
            if len(node_list) > 1:
                for i in range(len(node_list)):
                    for j in range(i + 1, min(i + 5, len(node_list))):
                        existing_links = self.trg.graph_db.links
                        duplicate = False
                        for link in existing_links.values():
                            if (link.source_node_id == node_list[i] and
                                link.target_node_id == node_list[j] and
                                link.properties.get('sub_type') == 'SAME_ENTITY'):
                                duplicate = True
                                break

                        if not duplicate:
                            link = Link(
                                source_node_id=node_list[i],
                                target_node_id=node_list[j],
                                link_type=LinkType.SEMANTIC,
                                properties={
                                    'sub_type': 'SAME_ENTITY',
                                    'entity': entity,
                                    'confidence': 0.9
                                }
                            )
                            self.trg.graph_db.add_link(link)
                            created += 1

        return created

    def create_temporal_proximity_links(self, nodes: List[str], max_time_diff_hours: int = 24,
                                        *, latest_only: bool = False) -> int:
        """MAGMA proximity links; optionally add only edges to the final node."""
        created = 0

        start = max(0, len(nodes) - 10) if latest_only else 0
        for i in range(start, len(nodes)):
            curr = self.trg.graph_db.get_node(nodes[i])
            if not curr or not hasattr(curr, 'timestamp') or not curr.timestamp:
                continue

            # Look ahead up to 10 nodes
            for j in range(i + 1, min(i + 10, len(nodes))):
                if latest_only and j != len(nodes) - 1:
                    continue
                next_node = self.trg.graph_db.get_node(nodes[j])
                if not next_node or not hasattr(next_node, 'timestamp') or not next_node.timestamp:
                    continue

                # Calculate time difference in hours
                time_diff = abs((next_node.timestamp - curr.timestamp).total_seconds() / 3600)

                if time_diff <= max_time_diff_hours:
                    # Weight inversely proportional to time distance
                    weight = 1.0 / (1.0 + time_diff)

                    link = Link(
                        source_node_id=nodes[i],
                        target_node_id=nodes[j],
                        link_type=LinkType.TEMPORAL,
                        properties={
                            'sub_type': 'TEMPORALLY_CLOSE',
                            'time_diff_hours': time_diff,
                            'weight': weight
                        }
                    )
                    self.trg.graph_db.add_link(link)
                    created += 1

        return created

    def detect_qa_links(self, nodes: List[str]) -> int:
        """Detect question-answer pairs and link them."""
        created = 0
        question_indicators = ['what', 'when', 'where', 'who', 'why', 'how', 'which',
                              'is', 'are', 'do', 'does', 'did', 'can', 'could', 'would', 'should',
                              'any', 'got', 'have you', 'did you', 'do you']

        for i in range(len(nodes) - 1):
            curr = self.trg.graph_db.get_node(nodes[i])
            next_node = self.trg.graph_db.get_node(nodes[i + 1])

            if curr and next_node:
                curr_text = ''
                if hasattr(curr, 'content_narrative'):
                    curr_text = curr.content_narrative.lower()
                elif hasattr(curr, 'summary'):
                    curr_text = curr.summary.lower()

                is_question = '?' in curr_text

                if not is_question:
                    for qw in question_indicators:
                        if f' {qw} ' in curr_text or curr_text.startswith(f'{qw} '):
                            is_question = True
                            break

                if is_question:
                    curr_speaker = curr.attributes.get('speaker', '') if hasattr(curr, 'attributes') else ''
                    next_speaker = next_node.attributes.get('speaker', '') if hasattr(next_node, 'attributes') else ''

                    confidence = 0.85 if curr_speaker != next_speaker else 0.7

                    link = Link(
                        source_node_id=nodes[i],
                        target_node_id=nodes[i + 1],
                        link_type=LinkType.CAUSAL,
                        properties={
                            'sub_type': 'ANSWERED_BY',
                            'confidence': confidence,
                            'is_qa_pair': True
                        }
                    )
                    self.trg.graph_db.add_link(link)
                    created += 1

        return created

    def create_episode_event_links(self, episode_id: str, event_ids: List[str]) -> int:
        """Create CONTAINS links between Episode and its Event nodes."""
        created = 0
        from .graph_db import LinkSubType

        for event_id in event_ids:
            # Episode CONTAINS Event
            link = Link(
                source_node_id=episode_id,
                target_node_id=event_id,
                link_type=LinkType.SEMANTIC,
                properties={
                    'sub_type': 'CONTAINS',
                    'confidence': 1.0
                }
            )
            self.trg.graph_db.add_link(link)
            created += 1

            # Event PART_OF Episode
            reverse_link = Link(
                source_node_id=event_id,
                target_node_id=episode_id,
                link_type=LinkType.SEMANTIC,
                properties={
                    'sub_type': 'PART_OF',
                    'confidence': 1.0
                }
            )
            self.trg.graph_db.add_link(reverse_link)
            created += 1

        return created

    def create_session_links(self) -> int:
        """Create BELONGS_TO_SESSION links between Events and their Session nodes."""
        created = 0

        for session_id, event_ids in self.session_event_map.items():
            if session_id not in self.session_nodes:
                continue

            session_node_id = self.session_nodes[session_id]

            for event_id in event_ids:
                link = Link(
                    source_node_id=event_id,
                    target_node_id=session_node_id,
                    link_type=LinkType.SEMANTIC,
                    properties={
                        'sub_type': LinkSubType.BELONGS_TO_SESSION.value,
                        'confidence': 1.0
                    }
                )
                self.trg.graph_db.add_link(link)
                created += 1

        return created

    def batch_create_links(self, node_ids: List[str]) -> Dict:
        """
        Create all link types between nodes in batch.

        Args:
            node_ids: List of node IDs

        Returns:
            Statistics about created links
        """
        stats = {
            'temporal': 0,
            'semantic': 0,
            'causal': 0,
            'entity': 0,
            'temporal_proximity': 0,
            'qa_pairs': 0,
            'context': 0
        }

        if not node_ids:
            return stats

        stats['temporal'] = self.create_temporal_links(node_ids)

        stats['context'] = self.create_context_links(node_ids, window_size=3)

        stats['semantic'] = self.create_semantic_links(node_ids)

        stats['causal'] = self.create_causal_links(node_ids)

        stats['entity'] = self.create_entity_links(node_ids)

        stats['temporal_proximity'] = self.create_temporal_proximity_links(node_ids)

        stats['qa_pairs'] = self.detect_qa_links(node_ids)

        return stats

    def build_memory(self, sample) -> Dict:
        """
        Build Jev-Mem memory from a LoCoMo sample.

        Args:
            sample: LoCoMoSample object

        Returns:
            Statistics about the built memory
        """
        if self.jev_config.write_enabled:
            created = rejected = 0
            for session_id in sorted(sample.conversation.sessions):
                session = sample.conversation.sessions[session_id]
                timestamp = self.temporal_parser.parse_session_timestamp(session.date_time)
                for turn in tqdm(session.turns, desc="Jev-Mem construction"):
                    node = self.build(f"[{turn.speaker}]: {turn.text}", timestamp=timestamp, metadata={
                        "speaker": turn.speaker, "session_id": session_id, "dia_id": turn.dia_id,
                        "source": str(sample.sample_id), "parent_interaction_id": turn.dia_id,
                        "original_text": turn.text})
                    if node is None:
                        rejected += 1
                    else:
                        created += 1
                        self.session_event_map.setdefault(session_id, []).append(node.node_id)
            return {**self.trg.get_statistics(), "events_created": created, "events_rejected": rejected}

        stats = self.trg.get_statistics()
        stats['events_created'] = 0
        logger.info(f"Starting memory build. Initial stats: {stats}")

        logger.info("Creating SESSION nodes from session summaries...")
        self.create_session_nodes(sample)
        logger.info(f"Created {len(self.session_nodes)} SESSION nodes")

        total_turns = sum(
            len(session.turns)
            for session in sample.conversation.sessions.values()
        )

        pbar = tqdm(total=total_turns, desc="Building memory")
        created_event_ids = []
        episode_buffer_events = []

        orig_temporal = self.trg._create_temporal_links
        orig_semantic = self.trg._create_semantic_links
        self.trg._create_temporal_links = lambda node: None
        self.trg._create_semantic_links = lambda node, top_k=3: None

        if self.use_episodes and self.episode_segmenter:
            self.episode_segmenter.reset()

        for session_id in sorted(sample.conversation.sessions.keys()):
            session = sample.conversation.sessions[session_id]
            timestamp = self.temporal_parser.parse_session_timestamp(session.date_time)

            turns_list = list(session.turns)

            for i, turn in enumerate(turns_list):
                prev_turn = turns_list[i - 1] if i > 0 else None
                next_turn = turns_list[i + 1] if i < len(turns_list) - 1 else None

                event_data = self.extract_event(turn, session_id, timestamp, prev_turn, next_turn)

                try:
                    event_id = self.trg.add_event(
                        interaction_content=event_data['content'],
                        timestamp=event_data['timestamp'],
                        metadata=event_data['metadata']
                    )
                    stats['events_created'] += 1
                    created_event_ids.append(event_id)

                    if session_id in self.session_event_map:
                        self.session_event_map[session_id].append(event_id)

                    self.index_event(
                        event_id,
                        event_data['metadata'].get('original_text', ''),
                        event_data['metadata']
                    )

                    if self.use_episodes:
                        episode_buffer_events.append(event_id)

                except Exception as e:
                    logger.error(f"Failed to add event: {e}")

                if self.use_episodes and self.episode_segmenter:
                    turn_data = {
                        'speaker': turn.speaker,
                        'text': turn.text,
                        'timestamp': event_data['timestamp'].isoformat(),
                        'dia_id': turn.dia_id,
                        'entities': event_data['metadata'].get('entities', []),
                        'topic': event_data['metadata'].get('topic', ''),
                        'dates_mentioned': event_data['metadata'].get('dates_mentioned', [])
                    }

                    episode = self.episode_segmenter.process_turn(turn_data)
                    if episode:
                        episode_id = self.create_episode_node(episode, session_id, episode_buffer_events)
                        self.create_episode_event_links(episode_id, episode_buffer_events)
                        episode_buffer_events = []

                pbar.update(1)
                pbar.set_postfix({'Events': stats['events_created'], 'Episodes': len(self.episode_nodes)})

        if self.use_episodes and self.episode_segmenter:
            final = self.episode_segmenter.finalize()
            if final and episode_buffer_events:
                episode_id = self.create_episode_node(final, session_id, episode_buffer_events)
                self.create_episode_event_links(episode_id, episode_buffer_events)

        pbar.close()

        self.trg._create_temporal_links = orig_temporal
        self.trg._create_semantic_links = orig_semantic

        logger.info("Creating batch links...")
        link_stats = self.batch_create_links(created_event_ids)

        logger.info("Creating session links...")
        session_links = self.create_session_links()
        link_stats['session_links'] = session_links
        logger.info(f"Created {session_links} BELONGS_TO_SESSION links")

        if self.use_episodes and self.episode_nodes:
            episode_link_stats = self.batch_create_links(self.episode_nodes)
            link_stats['episode_temporal'] = episode_link_stats['temporal']
            link_stats['episode_semantic'] = episode_link_stats['semantic']
            link_stats['episode_causal'] = episode_link_stats['causal']

        final_stats = self.trg.get_statistics()
        final_stats['link_breakdown'] = link_stats

        total_links_created = sum(link_stats.values())
        final_stats['links_created'] = total_links_created

        if self.use_episodes:
            final_stats['episode_count'] = len(self.episode_nodes)
            final_stats['events_per_episode'] = (
                stats['events_created'] / len(self.episode_nodes)
                if self.episode_nodes else 0
            )

        logger.info(f"Memory build complete. Final stats: {final_stats}")
        return final_stats

    def save(self):
        """Save memory to cache directory."""
        # Ensure directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Save graph database
        graph_path = self.cache_dir / "graph.json"
        self.trg.graph_db.save(str(graph_path))

        # Save vector database
        vector_path = self.cache_dir / "vectors"
        self.trg.vector_db.save(str(vector_path))

        # Save keyword index
        index_path = self.cache_dir / "keyword_index.json"
        with open(index_path, 'w') as f:
            # Convert sets to lists for JSON serialization
            index_data = {k: list(v) for k, v in self.node_index.items()}
            json.dump(index_data, f, indent=2)

        if self.jev_config.write_enabled or self.jev_config.read_enabled:
            with (self.cache_dir / "jev_mem_config.json").open('w') as f:
                json.dump(self.jev_config.to_dict(), f, indent=2)

        # Save episode information if using episodes
        if self.use_episodes:
            episode_path = self.cache_dir / "episodes.json"
            episode_data = {
                'episode_nodes': self.episode_nodes,
                'episode_event_map': self.episode_event_map
            }
            with open(episode_path, 'w') as f:
                json.dump(episode_data, f, indent=2)

        logger.info(f"Memory saved to {self.cache_dir}")

    def load(self, cache_dir=None):
        """Load memory from cache directory."""
        source_dir = Path(cache_dir) if cache_dir is not None else self.cache_dir
        graph_path = source_dir / "graph.json"
        if graph_path.exists():
            from .cache_compat import validate_cached_backend
            validate_cached_backend(source_dir, self.jev_config)
            self.trg.graph_db.load(str(graph_path))

        vector_path = source_dir / "vectors"
        if vector_path.exists():
            self.trg.vector_db.load(str(vector_path))

        index_path = source_dir / "keyword_index.json"
        if index_path.exists():
            with open(index_path, 'r') as f:
                index_data = json.load(f)
                self.node_index = {k: set(v) for k, v in index_data.items()}

        episode_path = source_dir / "episodes.json"
        if episode_path.exists():
            with open(episode_path, 'r') as f:
                episode_data = json.load(f)
                self.episode_nodes = episode_data.get('episode_nodes', [])
                self.episode_event_map = episode_data.get('episode_event_map', {})
            self.use_episodes = True

        self.trg.stats['events_added'] = sum(n.node_type == NodeType.EVENT for n in self.trg.graph_db.nodes.values())
        self.trg.stats['links_created'] = len(self.trg.graph_db.links)
        self._jev_writes = sum('admission' in n.attributes.get('jev_mem', {}) for n in self.trg.graph_db.nodes.values())

        logger.info(f"Memory loaded from {source_dir}")
        logger.info(f"Episodes mode: {self.use_episodes}, Episode nodes: {len(self.episode_nodes)}")

    def add_sessions_to_existing_memory(self, sample):
        """
        Add SESSION nodes to existing cached memory without rebuilding.
        This is much faster than rebuilding (~1 minute vs ~20 minutes).

        Args:
            sample: LoCoMoSample object with session_summary data
        """
        logger.info("Adding SESSION nodes to existing memory...")

        self.create_session_nodes(sample)

        for node_id, node_data in self.trg.graph_db.graph.nodes(data=True):
            if node_data.get('node_type') == 'EVENT':
                attributes = node_data.get('attributes', {})
                session_id = attributes.get('session_id')
                if session_id and session_id in self.session_nodes:
                    if session_id not in self.session_event_map:
                        self.session_event_map[session_id] = []
                    self.session_event_map[session_id].append(node_id)

        logger.info("Creating BELONGS_TO_SESSION links...")
        session_links = self.create_session_links()
        logger.info(f"Created {session_links} BELONGS_TO_SESSION links")

        self.save()
        logger.info(f"Updated memory with {len(self.session_nodes)} SESSION nodes")
