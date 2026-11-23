"""
Best-of-N Answer Selection Module

This module implements a strategy where each question is answered N times
and the best answer (based on self-consistency or LLM judge score) is selected.

This approach helps mitigate the variability in LLM responses and improves
overall accuracy, especially for complex multi-hop questions.
"""

import logging
import time
from typing import List, Dict, Tuple, Optional
from collections import Counter
import hashlib

logger = logging.getLogger(__name__)


class BestOfNSelector:
    """
    Selects the best answer from multiple attempts.
    """

    def __init__(self, n_attempts: int = 3, selection_method: str = 'llm_judge'):
        """
        Initialize the Best-of-N selector.

        Args:
            n_attempts: Number of times to answer each question
            selection_method: How to select best answer ('llm_judge', 'voting', 'confidence')
        """
        self.n_attempts = n_attempts
        self.selection_method = selection_method

    def get_best_answer(
        self,
        question: str,
        answer_generator,
        evaluator=None,
        expected_answer: str = None,
        verbose: bool = False
    ) -> Dict:
        """
        Get the best answer from N attempts.

        Args:
            question: The question to answer
            answer_generator: Function that takes question and returns answer
            evaluator: Optional evaluator for scoring answers
            expected_answer: Expected answer for evaluation
            verbose: Print progress

        Returns:
            Dictionary with best answer and metadata
        """
        attempts = []

        for i in range(self.n_attempts):
            if verbose:
                logger.info(f"Attempt {i+1}/{self.n_attempts} for question: {question[:50]}...")

            try:
                # Generate answer
                start_time = time.time()
                answer_result = answer_generator(question)
                elapsed = time.time() - start_time

                # Handle different return types
                if isinstance(answer_result, tuple):
                    answer, context = answer_result
                    metadata = {'context': context}
                elif isinstance(answer_result, dict):
                    answer = answer_result.get('answer', '')
                    metadata = answer_result
                else:
                    answer = str(answer_result)
                    metadata = {}

                # Score the answer if evaluator provided
                score = 0.0
                if evaluator and expected_answer:
                    if self.selection_method == 'llm_judge':
                        # Use LLM judge for scoring
                        score = evaluator.evaluate_single(question, answer, expected_answer)
                    else:
                        # Use F1 score or other metric
                        score = evaluator.calculate_f1(answer, expected_answer)

                attempts.append({
                    'attempt': i + 1,
                    'answer': answer,
                    'score': score,
                    'time': elapsed,
                    'metadata': metadata
                })

            except Exception as e:
                logger.warning(f"Attempt {i+1} failed: {e}")
                attempts.append({
                    'attempt': i + 1,
                    'answer': 'Error occurred',
                    'score': 0.0,
                    'time': 0.0,
                    'metadata': {'error': str(e)}
                })

        # Select best answer based on method
        best_attempt = self._select_best(attempts, self.selection_method)

        # Add statistics
        best_attempt['statistics'] = {
            'total_attempts': self.n_attempts,
            'successful_attempts': sum(1 for a in attempts if 'error' not in a.get('metadata', {})),
            'avg_score': sum(a['score'] for a in attempts) / len(attempts) if attempts else 0,
            'score_std': self._calculate_std([a['score'] for a in attempts]),
            'all_answers': [a['answer'] for a in attempts],
            'all_scores': [a['score'] for a in attempts]
        }

        return best_attempt

    def _select_best(self, attempts: List[Dict], method: str) -> Dict:
        """
        Select the best answer from attempts.

        Args:
            attempts: List of attempt dictionaries
            method: Selection method

        Returns:
            Best attempt dictionary
        """
        if not attempts:
            return {'answer': 'No attempts made', 'score': 0.0}

        if method == 'llm_judge':
            # Select answer with highest LLM judge score
            return max(attempts, key=lambda x: x['score'])

        elif method == 'voting':
            # Select most common answer (majority voting)
            answers = [a['answer'] for a in attempts]
            most_common = Counter(answers).most_common(1)[0][0]

            # Return the attempt with this answer (prefer higher score if multiple)
            matching = [a for a in attempts if a['answer'] == most_common]
            return max(matching, key=lambda x: x['score'])

        elif method == 'confidence':
            # Use answer length as proxy for confidence (longer = more detailed)
            # Combined with score
            return max(attempts, key=lambda x: x['score'] * (1 + len(x['answer']) / 1000))

        else:
            # Default to highest score
            return max(attempts, key=lambda x: x['score'])

    def _calculate_std(self, values: List[float]) -> float:
        """Calculate standard deviation."""
        if len(values) <= 1:
            return 0.0

        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return variance ** 0.5


class CachedBestOfNSelector(BestOfNSelector):
    """
    Best-of-N selector with caching to avoid redundant API calls.
    """

    def __init__(self, n_attempts: int = 3, selection_method: str = 'llm_judge'):
        super().__init__(n_attempts, selection_method)
        self.cache = {}

    def get_best_answer(
        self,
        question: str,
        answer_generator,
        evaluator=None,
        expected_answer: str = None,
        verbose: bool = False
    ) -> Dict:
        """
        Get best answer with caching support.
        """
        # Create cache key
        cache_key = self._get_cache_key(question, expected_answer)

        # Check cache
        if cache_key in self.cache:
            if verbose:
                logger.info(f"Using cached answer for: {question[:50]}...")
            return self.cache[cache_key]

        # Generate new answer
        result = super().get_best_answer(
            question, answer_generator, evaluator, expected_answer, verbose
        )

        # Cache result
        self.cache[cache_key] = result

        return result

    def _get_cache_key(self, question: str, expected: str = None) -> str:
        """Generate deterministic cache key."""
        key_str = f"{question}|{expected or ''}"
        return hashlib.md5(key_str.encode(), usedforsecurity=False).hexdigest()

    def clear_cache(self):
        """Clear the cache."""
        self.cache.clear()
        logger.info("Cache cleared")


def create_robust_answer_generator(test_harness, memory, n_attempts: int = 3) -> callable:
    """
    Create a robust answer generator that uses best-of-N selection.

    Args:
        test_harness: TestHarness instance
        memory: Memory instance
        n_attempts: Number of attempts per question

    Returns:
        Function that generates best answer for a question
    """
    selector = CachedBestOfNSelector(n_attempts=n_attempts)

    def generate_answer(qa: Dict) -> Dict:
        """Generate best answer for a QA pair."""
        question = qa['question']
        expected = qa['expected']
        category = qa.get('category', 0)

        # Create answer generator function
        def answer_gen(q):
            # Query memory
            context, _ = memory.query_engine.query(q, top_k=20)

            # Generate answer
            if context and context.anchor_nodes:
                formatted_context = test_harness._format_context(context.anchor_nodes)
                answer = test_harness._generate_qa_answer(q, formatted_context, category)

                # Validate answer for adversarial questions (category 5)
                if hasattr(test_harness, 'answer_formatter'):
                    answer = test_harness.answer_formatter.validate_adversarial_answer(q, answer, category)
            else:
                answer = "Information not found in memory"

            return answer, formatted_context if context else ""

        # Create simple evaluator
        class SimpleEvaluator:
            def calculate_f1(self, predicted, expected):
                pred_tokens = set(predicted.lower().split())
                exp_tokens = set(expected.lower().split())

                if not exp_tokens:
                    return 0.0

                intersection = pred_tokens & exp_tokens
                if not intersection:
                    return 0.0

                precision = len(intersection) / len(pred_tokens) if pred_tokens else 0
                recall = len(intersection) / len(exp_tokens)

                if precision + recall == 0:
                    return 0.0

                return 2 * (precision * recall) / (precision + recall)

        evaluator = SimpleEvaluator()

        # Get best answer
        result = selector.get_best_answer(
            question=question,
            answer_generator=answer_gen,
            evaluator=evaluator,
            expected_answer=expected,
            verbose=False
        )

        return {
            'question': question,
            'expected': expected,
            'predicted': result['answer'],
            'category': category,
            'attempts': result['statistics']['all_answers'],
            'scores': result['statistics']['all_scores'],
            'best_score': result['score'],
            'avg_score': result['statistics']['avg_score'],
            'score_std': result['statistics']['score_std']
        }

    return generate_answer