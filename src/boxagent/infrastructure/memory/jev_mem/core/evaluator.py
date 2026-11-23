"""
Evaluator Module

Handles all evaluation and scoring operations independently from memory building and querying.
This module can be used standalone for evaluating any QA system outputs.

Key Features:
- LLM-based answer evaluation
- Metrics calculation (F1, BLEU, Exact Match)
- Category-aware scoring
- Result aggregation and statistics
"""

import logging
from typing import Dict, Optional, List
from boxagent.infrastructure.memory.jev_mem.utils.utils import calculate_metrics
from .llm_judge import LLMJudge

logger = logging.getLogger(__name__)

class Evaluator:
    """
    Independent evaluation module for answer quality assessment.

    Can be used to evaluate answers from any QA system, not just TRG memory.
    """

    def __init__(self, llm_controller=None, use_llm_judge: bool = True):
        """
        Initialize evaluator.

        Args:
            llm_controller: Optional LLM controller for LLM judge
            use_llm_judge: Whether to use LLM judge for evaluation
        """
        self.llm_judge = None
        self.use_llm_judge = use_llm_judge

        if use_llm_judge and llm_controller:
            self.llm_judge = LLMJudge(llm_controller=llm_controller)
            logger.info("Evaluator initialized with LLM judge")
        else:
            logger.info("Evaluator initialized without LLM judge (metrics only)")

    def evaluate_answer(
        self,
        question: str,
        gold_answer: str,
        predicted_answer: str,
        question_category: Optional[int] = None
    ) -> Dict:
        """
        Evaluate a single answer with multiple metrics.

        Args:
            question: Question text
            gold_answer: Expected/gold standard answer
            predicted_answer: Predicted/generated answer
            question_category: Optional category for category-aware scoring

        Returns:
            Dictionary containing:
            - metrics: Dict with exact_match, f1, bleu1
            - llm_judge_score: Float 0.0-1.0 (if LLM judge enabled)
            - is_correct: Boolean exact match result
        """
        result = {}

        if gold_answer:
            metrics = calculate_metrics(
                predicted_answer,
                str(gold_answer),
                category=question_category
            )
            result['metrics'] = metrics
            result['is_correct'] = metrics.get('exact_match', False)
        else:
            result['metrics'] = None
            result['is_correct'] = False

        if self.use_llm_judge and self.llm_judge and gold_answer:
            llm_result = self.llm_judge.evaluate_answer(
                question,
                str(gold_answer),
                predicted_answer,
                question_category=question_category
            )
            result['llm_judge_score'] = llm_result.get('score', 0.0)
            result['llm_judge_reasoning'] = llm_result.get('reasoning', '')
        else:
            result['llm_judge_score'] = 0.0
            result['llm_judge_reasoning'] = ''

        return result

    def evaluate_batch(
        self,
        questions: List[str],
        gold_answers: List[str],
        predicted_answers: List[str],
        categories: Optional[List[int]] = None
    ) -> List[Dict]:
        """
        Evaluate a batch of answers.

        Args:
            questions: List of questions
            gold_answers: List of gold answers
            predicted_answers: List of predicted answers
            categories: Optional list of question categories

        Returns:
            List of evaluation results
        """
        if categories is None:
            categories = [None] * len(questions)

        results = []
        for i, (q, gold, pred, cat) in enumerate(zip(
            questions, gold_answers, predicted_answers, categories
        )):
            eval_result = self.evaluate_answer(q, gold, pred, cat)
            eval_result['question'] = q
            eval_result['gold_answer'] = gold
            eval_result['predicted_answer'] = pred
            eval_result['category'] = cat
            results.append(eval_result)

        return results

    def compute_aggregate_stats(self, evaluation_results: List[Dict]) -> Dict:
        """
        Compute aggregate statistics from evaluation results.

        Args:
            evaluation_results: List of evaluation result dicts

        Returns:
            Dictionary with aggregate statistics
        """
        if not evaluation_results:
            return {}

        total = len(evaluation_results)
        correct = sum(1 for r in evaluation_results if r.get('is_correct', False))

        f1_scores = [r['metrics']['f1'] for r in evaluation_results
                     if r.get('metrics') and 'f1' in r['metrics']]
        bleu_scores = [r['metrics']['bleu1'] for r in evaluation_results
                       if r.get('metrics') and 'bleu1' in r['metrics']]
        llm_scores = [r['llm_judge_score'] for r in evaluation_results
                      if r.get('llm_judge_score', 0) >= 0]

        stats = {
            'total': total,
            'correct': correct,
            'accuracy': (correct / total * 100) if total > 0 else 0.0,
            'avg_f1': (sum(f1_scores) / len(f1_scores) * 100) if f1_scores else 0.0,
            'avg_bleu1': (sum(bleu_scores) / len(bleu_scores) * 100) if bleu_scores else 0.0,
            'avg_llm_judge': (sum(llm_scores) / len(llm_scores) * 100) if llm_scores else 0.0
        }

        return stats

    def compute_category_stats(self, evaluation_results: List[Dict]) -> Dict:
        """
        Compute category-wise statistics from evaluation results.

        Args:
            evaluation_results: List of evaluation result dicts

        Returns:
            Dictionary with per-category statistics
        """
        from collections import defaultdict

        category_stats = defaultdict(lambda: {
            'total': 0, 'correct': 0, 'f1': [], 'bleu1': [], 'llm': []
        })

        for r in evaluation_results:
            cat = r.get('category', 0)
            category_stats[cat]['total'] += 1

            if r.get('is_correct', False):
                category_stats[cat]['correct'] += 1

            if r.get('metrics'):
                category_stats[cat]['f1'].append(r['metrics'].get('f1', 0))
                category_stats[cat]['bleu1'].append(r['metrics'].get('bleu1', 0))

            if r.get('llm_judge_score', 0) >= 0:
                category_stats[cat]['llm'].append(r['llm_judge_score'])

        result = {}
        for cat, stats in category_stats.items():
            total = stats['total']
            correct = stats['correct']
            result[cat] = {
                'total': total,
                'correct': correct,
                'accuracy': (correct / total * 100) if total > 0 else 0.0,
                'avg_f1': (sum(stats['f1']) / len(stats['f1']) * 100) if stats['f1'] else 0.0,
                'avg_bleu1': (sum(stats['bleu1']) / len(stats['bleu1']) * 100) if stats['bleu1'] else 0.0,
                'avg_llm': (sum(stats['llm']) / len(stats['llm']) * 100) if stats['llm'] else 0.0
            }

        return result
