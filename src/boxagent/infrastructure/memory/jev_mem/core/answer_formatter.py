"""
Answer Formatter Module

Handles answer extraction and normalization for consistent output formatting.
Includes JSON extraction, format standardization, and answer cleaning.
"""

import re
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

class AnswerFormatter:
    """
    Formats and normalizes answers for consistent output.
    """

    def __init__(self):
        """Initialize the answer formatter with common patterns."""
        # Common prefixes to remove
        self.answer_prefixes = [
            "Answer:", "The answer is:", "Based on the context,",
            "According to the information,", "The information shows:",
            "From the context,", "It appears that:", "The text indicates:"
        ]

        # Identity term mappings
        self.identity_mappings = {
            "trans woman": "Transgender woman",
            "trans man": "Transgender man",
            "transgender woman": "Transgender woman",
            "transgender man": "Transgender man",
            "transwoman": "Transgender woman",
            "transman": "Transgender man"
        }

        # Common "not found" variations
        self.not_found_patterns = [
            "information not found",
            "not found",
            "no information",
            "cannot find",
            "not available",
            "not provided",
            "not mentioned",
            "no data",
            "unable to find"
        ]

    def extract_answer(self, response: str, question: str = "") -> str:
        """
        Extract and normalize answer from LLM response.

        Args:
            response: Raw LLM response
            question: Original question for context

        Returns:
            Normalized answer string
        """
        # Check if response looks like malformed JSON (starts with { but isn't valid)
        if response.strip().startswith('{'):
            # Try to extract from JSON first
            json_answer = self._extract_from_json(response)
            if json_answer:
                answer = json_answer
            else:
                # If JSON parsing failed but response starts with {,
                # it's likely malformed JSON - try to extract meaningful text
                answer = self._extract_from_malformed_json(response, question)
        else:
            # Try standard JSON extraction for embedded JSON
            json_answer = self._extract_from_json(response)
            answer = json_answer if json_answer else response

        # Normalize the answer
        answer = self._normalize_answer(answer, question)

        return answer

    def _extract_from_malformed_json(self, response: str, question: str) -> str:
        """
        Extract answer from malformed JSON responses.

        Handles cases like:
        - {'name': 'Max'} for yes/no questions
        - {'James': 'planned to...'} for multi-entity questions

        Args:
            response: Malformed JSON response
            question: Original question for context

        Returns:
            Extracted answer string
        """
        question_lower = question.lower()

        # For yes/no questions, if we get any JSON response with content,
        # check if it indicates presence or absence
        if any(phrase in question_lower for phrase in ['do both', 'does', 'is', 'are', 'have both', 'has']):
            # If it's a yes/no question about existence
            if 'both' in question_lower:
                # For "Do both X and Y have..." questions
                # If we get specific data about items, it usually means "yes" for at least one
                # But we need to check the actual content

                # Try to extract any meaningful values from the malformed JSON
                import re
                # Look for quoted values
                values = re.findall(r"['\"]([^'\"]+)['\"]", response)

                # If we found actual pet names, items, etc., check if it's for both entities
                if values:
                    # Check if response mentions both entities
                    entities = []
                    words = question.split()
                    for i, word in enumerate(words):
                        if word[0].isupper() and word.lower() not in {'do', 'does', 'both', 'have', 'has'}:
                            entities.append(word.lower())

                    # If response contains data for multiple entities, answer is "Yes"
                    # If only one entity, answer is "No" (not both)
                    response_lower = response.lower()
                    entities_mentioned = sum(1 for e in entities if e in response_lower)

                    if entities_mentioned >= 2:
                        return "Yes"
                    elif entities_mentioned == 1:
                        return "No"  # Only one has it, not both
                    else:
                        # Generic data without entity attribution
                        # For "Do both have pets?" with response like {'name': 'Max'},
                        # this likely means only one has pets
                        return "No"

            # For simple yes/no questions
            if '{' in response and '}' in response:
                # If there's any structured data, it usually means "yes"
                # Empty or error responses would not have JSON structure
                if 'none' in response.lower() or 'null' in response.lower():
                    return "No"
                elif len(response) > 10:  # Has some content
                    return "Yes"

        # For counting questions (How many...)
        if 'how many' in question_lower:
            # Try to count items in the response
            import re
            # Look for numbers
            numbers = re.findall(r'\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b', response.lower())
            if numbers:
                # Return the first number found
                num_map = {'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
                          'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10'}
                first_num = numbers[0]
                return num_map.get(first_num, first_num)

            # Count comma-separated items
            items = re.findall(r"['\"]([^'\"]+)['\"]", response)
            if items:
                return str(len(items))

        # For "which" or "what" questions expecting lists
        if 'which' in question_lower or 'what' in question_lower:
            # Extract all quoted values and return as comma-separated list
            import re
            values = re.findall(r"['\"]([^'\"]+)['\"]", response)

            # Filter out dictionary keys and keep only values
            filtered_values = []
            for v in values:
                # Skip if it looks like a key (single word followed by colon in original)
                if ':' not in response[response.find(v)-5:response.find(v)+len(v)+5]:
                    filtered_values.append(v)

            if filtered_values:
                return ', '.join(filtered_values)

        # Default: try to extract any quoted text
        import re
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", response)
        if quoted:
            # Return the longest quoted string (likely the most informative)
            return max(quoted, key=len)

        # If all else fails, return the response stripped of JSON-like characters
        cleaned = response.replace('{', '').replace('}', '').replace("'", '').replace('"', '')
        cleaned = cleaned.replace(':', ' ').strip()

        return cleaned if cleaned else "Information not found"

    def _extract_from_json(self, response: str) -> Optional[str]:
        """
        Extract answer from JSON response if present.

        Args:
            response: Response that may contain JSON

        Returns:
            Extracted answer or None
        """
        if '{' not in response:
            return None

        try:
            # Clean up JSON markdown if present
            if '```json' in response:
                response = response.replace('```json', '').replace('```', '')

            # Find JSON object boundaries
            json_start = response.find('{')
            json_end = response.rfind('}') + 1

            if json_end > json_start:
                json_str = response[json_start:json_end]
                parsed = json.loads(json_str)

                # Try common answer keys
                answer_keys = [
                    'answer', 'result', 'value', 'response',
                    'identity', 'date', 'time', 'location',
                    'name', 'field', 'fields', 'data', 'collects',
                    'books', 'items', 'goals', 'authors', 'cities'
                ]

                # First check for direct keys
                for key in answer_keys:
                    if key in parsed:
                        value = parsed[key]

                        # Handle lists - join all items
                        if isinstance(value, list):
                            items = [str(v) for v in value if v and str(v).lower() != 'none']
                            if items:
                                return ', '.join(items)
                        # Handle dicts (nested answers)
                        elif isinstance(value, dict):
                            # Try to extract all values from nested dict
                            extracted = []
                            for sub_key, sub_value in value.items():
                                if isinstance(sub_value, list):
                                    extracted.extend([str(v) for v in sub_value if v])
                                elif sub_value and str(sub_value).lower() != 'none':
                                    extracted.append(str(sub_value))
                            if extracted:
                                return ', '.join(extracted)
                        elif value and str(value).lower() not in ['none', 'null']:
                            return str(value)

                # For nested structures like {"John": {"collects": [...]}}
                if parsed and isinstance(parsed, dict):
                    all_values = []
                    for key, value in parsed.items():
                        if isinstance(value, dict):
                            # Recursively extract from nested dict
                            for sub_key, sub_value in value.items():
                                if isinstance(sub_value, list):
                                    all_values.extend([str(v) for v in sub_value if v and str(v).lower() != 'none'])
                                elif sub_value and str(sub_value).lower() not in ['none', 'null']:
                                    all_values.append(str(sub_value))
                        elif isinstance(value, list):
                            all_values.extend([str(v) for v in value if v and str(v).lower() != 'none'])
                        elif value and str(value).lower() not in ['none', 'null']:
                            all_values.append(str(value))

                    if all_values:
                        # Remove duplicates while preserving order
                        seen = set()
                        unique = []
                        for v in all_values:
                            if v.lower() not in seen:
                                seen.add(v.lower())
                                unique.append(v)
                        return ', '.join(unique)

        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"JSON extraction failed: {e}")

        return None

    def _normalize_answer(self, answer: str, question: str) -> str:
        """
        Normalize answer format based on question context.

        Args:
            answer: Raw answer text
            question: Original question

        Returns:
            Normalized answer
        """
        # Clean up the answer
        answer = answer.strip().strip('"\'')

        # Check for "not found" variations
        if self._is_not_found(answer):
            return "Information not found"

        # Remove common prefixes
        answer = self._remove_prefixes(answer)

        # Formatting must not remove list members, names, negation, explanations
        # or relative-date qualifiers. Only canonicalize a standalone full date.
        months = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        if re.fullmatch(rf"(?:\d{{1,2}} {months} \d{{4}}|{months} \d{{1,2}},? \d{{4}})\.?", answer, re.I):
            answer = self._normalize_date(answer)
        return self._general_normalization(answer)

    def _is_not_found(self, answer: str) -> bool:
        """
        Check if answer indicates information was not found.

        Args:
            answer: Answer text

        Returns:
            True if answer indicates "not found"
        """
        answer_lower = answer.lower().strip().rstrip('.!')
        return answer_lower in self.not_found_patterns

    def _remove_prefixes(self, answer: str) -> str:
        """
        Remove common answer prefixes.

        Args:
            answer: Answer text

        Returns:
            Answer without prefixes
        """
        for prefix in self.answer_prefixes:
            if answer.startswith(prefix):
                answer = answer[len(prefix):].strip()
                break

        return answer

    def _normalize_date(self, answer: str) -> str:
        """
        Normalize date format to "D Month YYYY".

        Args:
            answer: Date answer

        Returns:
            Normalized date
        """
        # Remove leading zeros from days (08 May -> 8 May)
        answer = re.sub(r'\b0(\d) ', r'\1 ', answer)

        # Extract date pattern if embedded in text
        date_match = re.search(r'\b(\d{1,2})\s+(\w+)\s+(\d{4})\b', answer)
        if date_match:
            return date_match.group(0)

        # Handle "Month DD, YYYY" format
        date_match = re.search(r'\b(\w+)\s+(\d{1,2}),?\s+(\d{4})\b', answer)
        if date_match:
            month = date_match.group(1)
            day = int(date_match.group(2))
            year = date_match.group(3)
            return f"{day} {month} {year}"

        return answer

    def _normalize_duration(self, answer: str, question: str) -> str:
        """
        Normalize duration answers.

        Args:
            answer: Duration answer
            question: Original question

        Returns:
            Normalized duration
        """
        # Extract duration pattern
        duration_patterns = [
            r'(\d+)\s+(years?|months?|weeks?|days?|hours?)',
            r'(a|an|one)\s+(year|month|week|day|hour)',
            r'(several|few|many)\s+(years?|months?|weeks?|days?)'
        ]

        for pattern in duration_patterns:
            match = re.search(pattern, answer, re.IGNORECASE)
            if match:
                duration = match.group(0)

                # Handle "a/an/one" -> "1"
                duration = re.sub(r'\b(a|an|one)\s+', '1 ', duration, flags=re.IGNORECASE)

                # Add "ago" if question asks "how long ago"
                if "ago" in question.lower() and "ago" not in duration.lower():
                    duration += " ago"

                return duration

        return answer

    def _normalize_identity(self, answer: str) -> str:
        """
        Normalize identity-related answers.

        Args:
            answer: Identity answer

        Returns:
            Normalized identity
        """
        answer_lower = answer.lower()

        # Check identity mappings
        for pattern, normalized in self.identity_mappings.items():
            if pattern in answer_lower:
                return normalized

        # Handle relationship status
        if "single" in answer_lower and ("relationship" in answer_lower or "dating" in answer_lower):
            return "Single"
        elif "married" in answer_lower:
            return "Married"
        elif "divorced" in answer_lower:
            return "Divorced"

        # Extract short answer from verbose response
        if len(answer) > 50:
            # Look for key identity terms
            for pattern, normalized in self.identity_mappings.items():
                if pattern in answer_lower:
                    return normalized

            # Try to extract just the identity term
            identity_match = re.search(r'\b(transgender\s+\w+|trans\s+\w+|single|married)\b',
                                      answer, re.IGNORECASE)
            if identity_match:
                return self._general_normalization(identity_match.group(0))

        return answer

    def _normalize_name(self, answer: str) -> str:
        """
        Normalize name answers.

        Args:
            answer: Name answer

        Returns:
            Normalized name
        """
        # Extract capitalized names
        name_match = re.search(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', answer)
        if name_match:
            return name_match.group(0)

        # Handle lists of names
        if ',' in answer:
            names = [n.strip() for n in answer.split(',')]
            # Capitalize each name properly
            normalized_names = []
            for name in names:
                words = name.split()
                capitalized = ' '.join(w.capitalize() for w in words if w)
                if capitalized:
                    normalized_names.append(capitalized)
            return ', '.join(normalized_names)

        return self._general_normalization(answer)

    def _general_normalization(self, answer: str) -> str:
        """
        Apply general answer normalization.

        Args:
            answer: Answer text

        Returns:
            Generally normalized answer
        """
        # Capitalize first letter for short answers
        if len(answer) < 50 and answer and answer[0].islower():
            words = answer.split()
            if words:
                words[0] = words[0].capitalize()
                answer = ' '.join(words)

        # Clean up extra whitespace
        answer = ' '.join(answer.split())

        # Remove trailing punctuation unless it's meaningful
        if answer.endswith('.') and answer.count('.') == 1:
            answer = answer[:-1]

        return answer

    def build_qa_prompt(self, context: str, question: str, use_enhanced: bool = True, category: int = None) -> str:
        """
        Build QA prompt for LLM answer generation.

        Args:
            context: Formatted context from nodes
            question: Question to answer
            use_enhanced: Use enhanced prompt with detailed instructions (default: True)
            category: Question category (1=multi-hop, 2=temporal, 3=open-domain, 4=single-hop, 5=adversarial)

        Returns:
            Complete prompt for LLM
        """
        # Check question type for special handling
        question_lower = question.lower()
        is_hypothetical = question_lower.startswith(('would', 'could', 'might'))
        is_open_domain = category == 3
        from .temporal_parser import TemporalParser
        is_temporal = category == 2 or (category is None and TemporalParser().is_temporal_question(question))
        is_multihop = category == 1
        is_singlehop = category == 4

        if use_enhanced:
            # Special handling for multi-hop questions (category 1)
            if is_multihop:
                prompt = f"""Connect facts across the context to answer this question.

{context}

QUESTION: {question}

MULTI-HOP INSTRUCTIONS:
1. Look at KEY FACTS section first if present
2. Connect related information about the same person/topic
3. For "both/all" questions: Find commonalities between people
4. For research/identity: Connect clues (e.g., "researched X" + "chose org for X" = X)
5. Answer format:
   - Lists: "item1, item2, item3"
   - Counts: "Three"
   - Yes/No: Start with "Yes" or "No"

ANSWER:"""
            # Special handling for ALL temporal questions (category 2)
            elif is_temporal:
                # Balanced temporal prompt - clear but complete
                prompt = f"""Extract temporal information from the context.

{context}

QUESTION: {question}

TEMPORAL RULES:
1. Locate the requested person's event in the original statements. Read adjacent
   dialogue turns to resolve references such as "it", "that book", and "seven years now".
   Distinguish repeated events using any month/year or other qualifier in the question.
2. Each [Conversation] date anchors relative expressions ONLY in that statement.
   It is not automatically the event date. Never borrow another statement's anchor.
3. Use the anchored time expressions below each statement when they describe the
   requested event. A relative expression IS time evidence; do not report missing
   information merely because the original text lacks an absolute date.
4. Preserve precision: "last week" means the week before that statement's date,
   not exactly seven days earlier. "Last weekend" remains a weekend. "Last month"
   is a calendar month, and "last year" is a year; do not invent a day.
5. For day-level evidence, return D Month YYYY. For week/weekend evidence, return
   an anchored expression such as "The week before [conversation date]".
   For month/year evidence, return only the supported month/year.
6. For "how long", give the supported duration, optionally with the start year if
   directly calculable. For "how long ago", give elapsed time rather than a date.
7. Consider all relevant statements, not just the first ranked memory. If the
   event and its time cannot be established from the supplied evidence, answer
   "Information not found". Do not guess an event date from proximity alone.

ANSWER (only the date/time/duration):"""
            # Special handling for single-hop questions (category 4) - SIMPLE FACTUAL QUESTIONS
            elif is_singlehop:
                prompt = f"""Find and extract the specific fact requested.

{context}

QUESTION: {question}

INSTRUCTIONS:
- Find the EXACT information requested
- Answer with the specific fact only (2-15 words typical)
- For "what": Extract the specific item/thing/activity
- For "who": Extract the name/person
- For "where": Extract the location
- For "when": Extract the date/time
- For "why": Extract the reason given
- For "how": Extract the method/way described
- Do NOT add explanations, only the fact

ANSWER:"""
            # Special handling for adversarial questions (category 5)
            elif category == 5:
                prompt = f"""Verify the EXACT entity exists before answering.

{context}

QUESTION: {question}

CRITICAL RULES:
1. Check if the EXACT person/entity in the question exists in context
2. If question asks about "Person A" but context only has "Person B" → "Information not found"
3. Do NOT make substitutions (e.g., Melanie ≠ Caroline)
4. When uncertain or entity mismatch → "Information not found"

ANSWER (be strict):"""
            # Special handling for open-domain questions
            elif is_open_domain or is_hypothetical:
                # Open-domain questions need inference and reasoning
                prompt = f"""Make reasonable inferences based on the context.

{context}

QUESTION: {question}

INFERENCE GUIDELINES:
1. For "Would X..." questions: Answer "Yes/No, because [brief reason]"
2. For personality traits: List 2-3 specific traits based on behavior
3. For preferences: Give specific answer based on their interests
4. Make reasonable inferences from available evidence
5. Be confident but base answers on context

ANSWER:"""
            else:
                # Balanced default prompt - clear but not verbose
                prompt = f"""Answer based on the conversation context.

{context}

QUESTION: {question}

INSTRUCTIONS:
1. Check KEY FACTS section first if present
2. Connect related information across memories
3. Verify correct person/entity is mentioned
4. For comparisons: Find commonalities or differences
5. Pay attention to who said what (speaker tags)
6. If information not found → "Information not found"
7. Answer concisely (5-6 words typical)

ANSWER:"""

        else:
            # Original simple prompt (kept as fallback)
            prompt = f"""You are answering a question based on conversation context. Use the following steps:

1. Read the context carefully
2. Identify key information related to the question
3. Reason about what the question is asking
4. Extract or infer the answer from the context
5. Format your answer concisely (prefer 5-6 words when possible)
6. Only include the direct answer, not explanations
7. If information is not in context, respond "Information not found"

Context:
{context}

Question: {question}

Answer (concise, direct):"""

        return prompt

    def _get_original_text(self, node) -> str:
        """
        Extract original text from node.

        Args:
            node: Event or Episode node

        Returns:
            Original text from conversation
        """
        # Try to get original_text from attributes
        if hasattr(node, 'attributes') and isinstance(node.attributes, dict):
            original = node.attributes.get('original_text', '')
            if original:
                return original

        # Handle EpisodeNode - get full content from attributes
        if hasattr(node, 'node_type') and str(node.node_type) == 'NodeType.EPISODE':
            # Episode node - get full session content from attributes
            if hasattr(node, 'attributes') and isinstance(node.attributes, dict):
                content = node.attributes.get('content', '')
                if content:
                    return content
            # Fallback to summary if content not in attributes
            if hasattr(node, 'summary'):
                return node.summary
        # Fallback to content_narrative if original_text not available
        elif hasattr(node, 'content_narrative'):
            return node.content_narrative
        elif hasattr(node, 'summary'):
            # Only use summary as last resort (for display, not ideal for QA)
            return node.summary
        else:
            return str(node)

    def _get_semantic_enrichment(self, node) -> str:
        """
        Extract semantic enrichment from node (facts, relationships, activities).

        Args:
            node: Event or Episode node

        Returns:
            Formatted semantic enrichment string or empty string
        """
        if not hasattr(node, 'attributes') or not isinstance(node.attributes, dict):
            return ""

        enrichment_parts = []

        # Extract semantic facts
        semantic_facts = node.attributes.get('semantic_facts', [])
        if semantic_facts:
            for fact in semantic_facts[:2]:  # Limit to top 2 facts
                enrichment_parts.append(f"    • {fact}")

        # Extract relationships
        relationships = node.attributes.get('relationships', [])
        if relationships:
            for rel in relationships[:2]:  # Limit to top 2 relationships
                enrichment_parts.append(f"    • {rel}")

        # Extract activities
        activities = node.attributes.get('activities', [])
        if activities:
            for activity in activities[:2]:  # Limit to top 2 activities
                enrichment_parts.append(f"    • {activity}")

        if enrichment_parts:
            return "\n" + "\n".join(enrichment_parts)
        return ""

    def validate_adversarial_answer(self, question: str, answer: str, category: int = None) -> str:
        """
        Validate answer for adversarial questions (category 5) to detect entity mismatches.

        Args:
            question: Original question
            answer: Generated answer
            category: Question category

        Returns:
            Validated answer (returns "Information not found" if mismatch detected)
        """
        # Only validate category 5 (adversarial) questions
        if category != 5:
            return answer

        # Skip if already returning "not found"
        if self._is_not_found(answer):
            return "Information not found"

        import re

        # Extract named entities (capitalized names) from question
        question_entities = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', question)
        question_entities_lower = [e.lower() for e in question_entities]

        # Extract entities from answer
        answer_entities = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', answer)
        answer_entities_lower = [e.lower() for e in answer_entities]

        # Check for entity mismatch
        if question_entities_lower and answer_entities_lower:
            # Check if any question entity appears in the answer
            entity_found = False
            for q_entity in question_entities_lower:
                for a_entity in answer_entities_lower:
                    if q_entity in a_entity or a_entity in q_entity:
                        entity_found = True
                        break
                if entity_found:
                    break

            # If question asks about specific entity but answer is about different entity
            if not entity_found:
                # This is likely a mismatch (e.g., asking about Melanie but answering about Caroline)
                logger.info(f"Entity mismatch detected in adversarial question: Question entities: {question_entities}, Answer entities: {answer_entities}")
                return "Information not found"

        # Additional validation: Check for conflicting information patterns
        # If answer seems too specific for an adversarial question, be suspicious
        suspicious_patterns = [
            r'\b(chose|selected|picked|decided)\b.*\b(because|for|due to)\b',  # Specific reasoning
            r'\b(did|was|had|went)\b.*\b(on|at|in)\b.*\d{4}',  # Specific dates
        ]

        for pattern in suspicious_patterns:
            if re.search(pattern, answer.lower()) and question_entities_lower:
                # Double-check if this specific info is about the right person
                answer_lower = answer.lower()
                has_correct_entity = any(entity in answer_lower for entity in question_entities_lower)
                if not has_correct_entity:
                    logger.info(f"Suspicious specific answer without correct entity for adversarial question")
                    return "Information not found"

        return answer

    def format_context_for_qa(self, nodes: List[Any], question: str, session_nodes: List[Any] = None) -> str:
        """
        Format retrieved nodes into context for QA.

        Args:
            nodes: List of retrieved event nodes
            question: Original question
            session_nodes: Optional list of SESSION nodes providing broader context

        Returns:
            Formatted context string
        """
        if not nodes:
            return "No relevant information found"

        context_parts = []

        # Detect question type for better formatting
        q_lower = question.lower()
        from .temporal_parser import TemporalParser
        temporal_parser = TemporalParser()
        is_temporal = temporal_parser.is_temporal_question(question)
        is_multihop = any(pattern in q_lower for pattern in [
            'research', 'identity', 'relationship', 'career', 'activities',
            'participate', 'involved', 'pursue', 'field', 'both', 'move from'
        ])

        if is_temporal:
            # Keep original text, attribution and the date anchor together. Relative
            # dates are derived on read as well, so existing graphs remain usable.
            context_parts.append("Temporal evidence (each statement has its own conversation date):")
            # Keep contiguous dialogue turns together so a question or named
            # object is not separated from its date-bearing reply by rank order.
            groups, ungrouped = {}, []
            for rank, node in enumerate(nodes):
                attrs = getattr(node, 'attributes', {})
                match = re.fullmatch(r'D(\d+):(\d+)', str(attrs.get('dia_id', '')))
                if match:
                    key = (attrs.get('source'), match.group(1))
                    groups.setdefault(key, []).append((int(match.group(2)), rank, node))
                else:
                    ungrouped.append((rank, [node]))
            blocks = ungrouped
            for entries in groups.values():
                current, ranks, previous = [], [], None
                for turn, rank, node in sorted(entries, key=lambda entry: entry[0]):
                    if previous is not None and turn != previous + 1:
                        blocks.append((min(ranks), current))
                        current, ranks = [], []
                    current.append(node)
                    ranks.append(rank)
                    previous = turn
                if current:
                    blocks.append((min(ranks), current))
            ordered = [node for _, block in sorted(blocks, key=lambda block: block[0]) for node in block]
            for i, node in enumerate(ordered, 1):
                content = self._get_original_text(node)
                attributes = getattr(node, 'attributes', {})
                speaker = attributes.get('speaker', 'unknown')
                dialogue = attributes.get('dia_id', node.node_id)
                date = temporal_parser.normalize_date_format(node.timestamp) if node.timestamp else 'unknown'
                context_parts.append(f"\n{i}. [Dialogue: {dialogue}] [Conversation: {date}] "
                                     f"[Speaker: {speaker}] {content}")
                references = temporal_parser.describe_references(content, node.timestamp)
                if references:
                    hints = '; '.join(f"{r['original']!r} → {r['normalized']} ({r['precision']} precision)"
                                      for r in references)
                    context_parts.append(f"   Anchored time expressions: {hints}")
            return '\n'.join(context_parts)

        if is_multihop:
            # Keep first-person statements attributable and relative dates anchored.
            context_parts.append("Full memories for multi-hop reasoning (ranked by relevance):")
            for i, node in enumerate(nodes, 1):
                content = self._get_original_text(node)
                attributes = getattr(node, "attributes", {})
                speaker = attributes.get("speaker", "unknown")
                dialogue = attributes.get("dia_id", node.node_id)
                date = node.timestamp.strftime("%d %B %Y") if node.timestamp else "unknown"
                enrichment = self._get_semantic_enrichment(node)
                context_parts.append(f"\n{i}. [Dialogue: {dialogue}] [Conversation: {date}] "
                                     f"[Speaker: {speaker}] {content}{enrichment}")


        else:
            # Format as relevant information list - PRIORITIZE TOP NODES
            context_parts.append("Information ranked by relevance (MOST relevant first):")

            # Special formatting for top nodes
            for i, node in enumerate(nodes, 1):  # Use all retrieved nodes (includes Q&A pairs)
                # Handle both EventNode and EpisodeNode
                content = self._get_original_text(node)

                # Include speaker information if available
                speaker = ""
                if hasattr(node, 'attributes') and 'speaker' in node.attributes:
                    speaker = f"[Speaker: {node.attributes['speaker']}] "

                # Mark the most relevant nodes clearly
                if i == 1:
                    relevance_marker = "**MOST RELEVANT** "
                elif i == 2:
                    relevance_marker = "*Highly Relevant* "
                elif i <= 4:
                    relevance_marker = "*Relevant* "
                elif i <= 6:
                    relevance_marker = "Somewhat relevant "
                else:
                    relevance_marker = ""

                # Add semantic enrichment after main content
                enrichment = self._get_semantic_enrichment(node)

                if hasattr(node, 'timestamp') and node.timestamp:
                    date_str = node.timestamp.strftime('%d %B %Y')
                    context_parts.append(f"\n{i}. {relevance_marker}[{date_str}] {speaker}{content}{enrichment}")
                else:
                    context_parts.append(f"\n{i}. {relevance_marker}{speaker}{content}{enrichment}")

                # Add original text if short and relevant
                if hasattr(node, 'attributes') and 'original_text' in node.attributes:
                    orig_text = node.attributes['original_text']
                    if len(orig_text) < 200:
                        # Check if original contains question keywords
                        question_words = set(question.lower().split())
                        orig_words = set(orig_text.lower().split())
                        if len(question_words & orig_words) >= 2:
                            context_parts.append(f"   Original: {orig_text}")

        # Add most relevant excerpts section
        if nodes and len(nodes) > 0:
            context_parts.append("\n\n=== Key Information ===")
            question_keywords = [w for w in question.lower().split()
                               if len(w) > 3 and w not in {'what', 'when', 'where', 'who', 'how'}]

            excerpts_added = 0
            for node in nodes:
                if excerpts_added >= 5:
                    break

                if hasattr(node, 'attributes') and 'original_text' in node.attributes:
                    text = node.attributes['original_text']
                    sentences = text.split('.')

                    for sent in sentences:
                        sent_lower = sent.lower()
                        # Check if sentence contains question keywords
                        if any(kw in sent_lower for kw in question_keywords):
                            context_parts.append(f"• {sent.strip()}")
                            excerpts_added += 1
                            if excerpts_added >= 5:
                                break

        return "\n".join(context_parts)

    def validate_answer(self, answer: str, expected_format: Optional[str] = None) -> bool:
        """
        Validate if answer meets expected format requirements.

        Args:
            answer: Answer to validate
            expected_format: Optional expected format hint

        Returns:
            True if answer is valid
        """
        # Check for empty or not found
        if not answer or self._is_not_found(answer):
            return True  # Valid "not found" response

        # Check for specific format requirements
        if expected_format:
            if expected_format == "date":
                # Should match date pattern
                return bool(re.search(r'\b\d{1,2}\s+\w+\s+\d{4}\b', answer))
            elif expected_format == "duration":
                # Should match duration pattern
                return bool(re.search(r'\d+\s+(years?|months?|weeks?|days?)', answer))
            elif expected_format == "name":
                # Should start with capital letter
                return bool(answer) and answer[0].isupper()

        return True
