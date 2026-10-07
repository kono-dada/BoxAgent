"""
Episode Segmentation Module for Jev-Mem

Groups related conversation turns into semantic episodes using LLM-based boundary detection.
Based on Nemori's episode segmentation approach with semantic boundaries.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any

@dataclass
class Episode:
    """
    Represents a semantic episode composed of multiple conversation turns.
    """
    title: str
    content: str
    original_messages: List[Dict[str, Any]]
    participants: List[str]
    start_timestamp: Optional[datetime] = None
    end_timestamp: Optional[datetime] = None
    message_count: int = 0
    boundary_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    episode_id: str = field(default_factory=lambda: f"episode_{datetime.now().timestamp()}")

class MessageBuffer:
    """
    Accumulates conversation turns until a semantic boundary is detected.
    """
    def __init__(self, max_buffer_size: int = 10):
        self.messages: List[Dict[str, Any]] = []
        self.max_buffer_size = max_buffer_size

    def add(self, turn: Any) -> None:
        """Add a conversation turn to the buffer."""
        # Handle both dict and object inputs
        if isinstance(turn, dict):
            message = {
                'speaker': turn.get('speaker', 'Unknown'),
                'text': turn.get('text', ''),
                'timestamp': turn.get('timestamp', None)
            }
        else:
            message = {
                'speaker': getattr(turn, 'speaker', 'Unknown'),
                'text': getattr(turn, 'text', ''),
                'timestamp': getattr(turn, 'timestamp', None)
            }
        self.messages.append(message)

    def size(self) -> int:
        """Return the number of messages in buffer."""
        return len(self.messages)

    def is_full(self) -> bool:
        """Check if buffer has reached max size."""
        return len(self.messages) >= self.max_buffer_size

    def clear(self) -> None:
        """Clear all messages from buffer."""
        self.messages.clear()

    def get_messages(self) -> List[Dict[str, Any]]:
        """Get all messages in buffer."""
        return self.messages.copy()

class BoundaryDetector:
    """
    Detects semantic boundaries in conversation using LLM analysis.
    """
    def __init__(self, llm_controller):
        # Extract the actual LLM backend from LLMController wrapper
        self.llm = llm_controller.llm if hasattr(llm_controller, 'llm') else llm_controller
        self.logger = logging.getLogger(__name__)

    def detect_boundary(self, buffer: MessageBuffer, new_turn: Any) -> tuple[bool, str, float]:
        """
        Detect if there's a semantic boundary between buffer and new turn.

        Returns:
            (is_boundary, reason, confidence)
        """
        # Fast path: Check explicit signals first
        explicit_boundary = self._check_explicit_signals(buffer, new_turn)
        if explicit_boundary:
            return True, "Explicit topic marker or time gap", 1.0

        # Semantic analysis using LLM
        if buffer.size() >= 2:  # Need at least 2 messages for context
            return self._semantic_boundary_check(buffer, new_turn)

        return False, "Insufficient context", 0.0

    def _check_explicit_signals(self, buffer: MessageBuffer, new_turn: Any) -> bool:
        """Check for explicit boundary signals (time gaps, topic markers)."""
        if buffer.size() == 0:
            return False

        if isinstance(new_turn, dict):
            turn_timestamp = new_turn.get('timestamp')
            turn_text = new_turn.get('text', '')
        else:
            turn_timestamp = getattr(new_turn, 'timestamp', None)
            turn_text = getattr(new_turn, 'text', '')

        last_msg = buffer.messages[-1]
        if last_msg.get('timestamp') and turn_timestamp:
            last_date = last_msg['timestamp'].date() if hasattr(last_msg['timestamp'], 'date') else None
            new_date = turn_timestamp.date() if hasattr(turn_timestamp, 'date') else None
            if last_date and new_date and last_date != new_date:
                return True

        topic_markers = [
            'by the way', 'anyway', 'changing the subject',
            'moving on', 'on another note', 'different topic'
        ]
        text_lower = turn_text.lower()
        for marker in topic_markers:
            if marker in text_lower:
                return True

        return False

    def _semantic_boundary_check(self, buffer: MessageBuffer, new_turn: Any) -> tuple[bool, str, float]:
        """Use LLM to detect semantic boundaries."""
        # Get turn data (handle both dict and object)
        if isinstance(new_turn, dict):
            turn_speaker = new_turn.get('speaker', 'Unknown')
            turn_text = new_turn.get('text', '')
        else:
            turn_speaker = getattr(new_turn, 'speaker', 'Unknown')
            turn_text = getattr(new_turn, 'text', '')

        # Build context from buffer
        context_lines = []
        for msg in buffer.messages[-5:]:  # Last 5 messages for context
            context_lines.append(f"{msg['speaker']}: {msg['text']}")
        context = "\n".join(context_lines)

        prompt = f"""Analyze if the new message marks a semantic boundary (topic shift) in the conversation.

Previous conversation:
{context}

New message:
{turn_speaker}: {turn_text}

Does this new message indicate a topic shift or should it be grouped with the previous conversation?

Consider:
- Topic continuity: Is the new message related to the previous topic?
- Semantic coherence: Does it follow naturally from the context?
- Contextual relevance: Does it reference or build on previous messages?

Return JSON with:
- "is_boundary": boolean (true if topic shift, false if continuation)
- "reason": string (one sentence explaining the decision)
- "confidence": float 0.0-1.0 (how confident you are)
"""

        try:
            response = self.llm.get_completion(
                prompt,
                response_format={"type": "json_object"},
                temperature=0.0
            )
            result = json.loads(response)
            is_boundary = result.get('is_boundary', False)
            reason = result.get('reason', 'No reason provided')
            confidence = float(result.get('confidence', 0.5))

            # Only create boundary on HIGH confidence
            if is_boundary and confidence >= 0.7:
                return True, reason, confidence

            return False, reason, confidence

        except Exception as e:
            self.logger.warning(f"Boundary detection failed: {e}")
            return False, f"Error: {str(e)}", 0.0

class EpisodeSegmenter:
    """
    Main orchestrator for episode-based segmentation.
    """
    def __init__(self, llm_controller, max_buffer_size: int = 10, min_episode_size: int = 2):
        """
        Args:
            llm_controller: LLM controller for boundary detection and summarization
            max_buffer_size: Force boundary after this many turns
            min_episode_size: Minimum turns required to create an episode
        """
        # Extract the actual LLM backend from LLMController wrapper
        self.llm = llm_controller.llm if hasattr(llm_controller, 'llm') else llm_controller
        self.max_buffer_size = max_buffer_size
        self.min_episode_size = min_episode_size
        self.buffer = MessageBuffer(max_buffer_size)
        self.boundary_detector = BoundaryDetector(llm_controller)
        self.logger = logging.getLogger(__name__)

    def process_turn(self, turn: Any) -> Optional[Episode]:
        """
        Process a conversation turn and return an Episode if boundary is detected.

        Args:
            turn: Conversation turn object with speaker, text, timestamp

        Returns:
            Episode if boundary detected, None otherwise
        """
        # Check if we should create an episode before adding new turn
        should_create = False
        boundary_reason = ""

        # Force boundary if buffer is full
        if self.buffer.is_full():
            should_create = True
            boundary_reason = f"Max buffer size ({self.max_buffer_size}) reached"
        # Check for semantic boundary
        elif self.buffer.size() > 0:
            is_boundary, reason, confidence = self.boundary_detector.detect_boundary(
                self.buffer, turn
            )
            if is_boundary:
                should_create = True
                boundary_reason = reason

        # Create episode if boundary detected and buffer has enough messages
        episode = None
        if should_create and self.buffer.size() >= self.min_episode_size:
            episode = self.create_episode(boundary_reason)
            self.buffer.clear()

        # Add new turn to buffer
        self.buffer.add(turn)

        return episode

    def flush_remaining(self) -> Optional[Episode]:
        """
        Create an episode from remaining messages in buffer.
        Call this at the end of processing.
        """
        if self.buffer.size() >= self.min_episode_size:
            episode = self.create_episode("End of conversation")
            self.buffer.clear()
            return episode
        return None

    def finalize(self) -> Optional[Episode]:
        """
        Alias for flush_remaining() - finalize episode processing.
        """
        return self.flush_remaining()

    def create_episode(self, boundary_reason: str = "") -> Episode:
        """
        Create an Episode from current buffer contents.
        """
        messages = self.buffer.get_messages()

        if not messages:
            raise ValueError("Cannot create episode from empty buffer")

        # Extract participants
        participants = list(set(msg['speaker'] for msg in messages))

        # Generate title and summary using LLM
        conversation_text = "\n".join([
            f"{msg['speaker']}: {msg['text']}" for msg in messages
        ])

        title, summary = self._generate_title_and_summary(conversation_text)

        # Extract timestamps
        timestamps = [msg['timestamp'] for msg in messages if msg.get('timestamp')]
        start_timestamp = min(timestamps) if timestamps else None
        end_timestamp = max(timestamps) if timestamps else None

        episode = Episode(
            title=title,
            content=summary,
            original_messages=messages,
            participants=participants,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            message_count=len(messages),
            boundary_reason=boundary_reason
        )

        return episode

    def _generate_title_and_summary(self, conversation_text: str) -> tuple[str, str]:
        """Generate episode title and summary using LLM."""
        prompt = f"""Generate a title and summary for this conversation episode.

Conversation:
{conversation_text}

Return JSON with:
- "title": A concise 3-7 word title capturing the main topic
- "summary": A 2-3 sentence summary of what was discussed

Keep the summary factual and preserve important details (names, places, dates, etc).
"""

        try:
            response = self.llm.get_completion(
                prompt,
                response_format={"type": "json_object"},
                temperature=0.0
            )
            result = json.loads(response)
            title = result.get('title', 'Untitled Episode')
            summary = result.get('summary', conversation_text[:200])
            return title, summary

        except Exception as e:
            self.logger.warning(f"Episode summarization failed: {e}")
            lines = conversation_text.split('\n')
            title = lines[0][:50] if lines else "Untitled Episode"
            summary = conversation_text[:200]
            return title, summary

    def reset(self) -> None:
        """Reset the segmenter state."""
        self.buffer.clear()
