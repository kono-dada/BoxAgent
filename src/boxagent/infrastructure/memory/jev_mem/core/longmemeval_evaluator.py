"""
LongMemEval Evaluator

Evaluates answers using question-type-specific prompts following LongMemEval standards.
Supports evaluation of different question types with appropriate rubrics.
"""

import os
from typing import Dict, Any, Optional
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

class LongMemEvalEvaluator:
    """LongMemEval Result Evaluator with question-type-specific prompts"""

    def __init__(self, model: str = "gpt-4o-mini"):
        """
        Initialize evaluator

        Args:
            model: Model used for evaluation
        """
        self.model = model
        self.client = OpenAI(base_url=os.getenv("OPENAI_BASE_URL") or None)

        # Evaluation prompt templates based on LongMemEval standards
        self.TEMPORAL_REASONING_PROMPT = """
I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct.

<QUESTION>
{question}
</QUESTION>
<CORRECT ANSWER>
{gold_answer}
</CORRECT ANSWER>
<RESPONSE>
{response}
</RESPONSE>

Answer with only 'yes' or 'no':
"""

        self.KNOWLEDGE_UPDATE_PROMPT = """
I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.

<QUESTION>
{question}
</QUESTION>
<CORRECT ANSWER>
{gold_answer}
</CORRECT ANSWER>
<RESPONSE>
{response}
</RESPONSE>

Answer with only 'yes' or 'no':
"""

        self.SINGLE_SESSION_PREFERENCE_PROMPT = """
I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.

<QUESTION>
{question}
</QUESTION>
<RUBRIC>
{gold_answer}
</RUBRIC>
<RESPONSE>
{response}
</RESPONSE>

Answer with only 'yes' or 'no':
"""

        self.DEFAULT_PROMPT = """
I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer or the key/core information from the correct answer. Otherwise, answer no.

Important evaluation guidelines:
- If the response contains the main factual content, answer yes (e.g., "University of Melbourne" is correct even if the gold answer is "University of Melbourne in Australia")
- Minor differences in articles (a/the), capitalization, or additional context should not affect correctness
- If the response captures the essential answer to the question, answer yes
- Only answer no if the response is factually wrong or completely missing the key information

<QUESTION>
{question}
</QUESTION>
<CORRECT ANSWER>
{gold_answer}
</CORRECT ANSWER>
<RESPONSE>
{response}
</RESPONSE>

Answer with only 'yes' or 'no':
"""

    def evaluate_single_response(self, question: str, gold_answer: str,
                                 response: str, question_type: str) -> Dict[str, Any]:
        """
        Evaluate single response

        Args:
            question: Question
            gold_answer: Gold standard answer
            response: Model response
            question_type: Question type

        Returns:
            Dictionary with is_correct (bool) and score (float)
        """
        system_prompt = """You are an expert grader that determines if answers to questions match a gold standard answer. Answer with only 'yes' or 'no'."""

        if question_type == 'temporal-reasoning':
            prompt = self.TEMPORAL_REASONING_PROMPT.format(
                question=question, gold_answer=gold_answer, response=response
            )
        elif question_type == 'knowledge-update':
            prompt = self.KNOWLEDGE_UPDATE_PROMPT.format(
                question=question, gold_answer=gold_answer, response=response
            )
        elif question_type == 'single-session-preference':
            prompt = self.SINGLE_SESSION_PREFERENCE_PROMPT.format(
                question=question, gold_answer=gold_answer, response=response
            )
        else:
            prompt = self.DEFAULT_PROMPT.format(
                question=question, gold_answer=gold_answer, response=response
            )

        try:
            response_obj = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                max_tokens=10
            )

            result_text = response_obj.choices[0].message.content.strip().lower()
            is_correct = result_text == 'yes' or result_text.startswith('yes')

            return {
                'is_correct': is_correct,
                'score': 1.0 if is_correct else 0.0,
                'raw_response': result_text
            }

        except Exception as e:
            print(f"Error evaluating response: {e}")
            return {
                'is_correct': False,
                'score': 0.0,
                'error': str(e)
            }

    def evaluate_answer(self, question: str, expected: str, predicted: str,
                        question_type: str = 'default') -> Dict[str, Any]:
        """
        Evaluate an answer (compatible interface with existing Evaluator)

        Args:
            question: The question
            expected: Expected answer
            predicted: Predicted answer
            question_type: Type of question

        Returns:
            Evaluation result dictionary
        """
        eval_result = self.evaluate_single_response(
            question=question,
            gold_answer=expected,
            response=predicted,
            question_type=question_type
        )

        return {
            'is_correct': eval_result['is_correct'],
            'llm_judge_score': eval_result['score'],
            'metrics': {
                'accuracy': eval_result['score'],
                'f1': eval_result['score'],
                'bleu1': eval_result['score']
            }
        }

    def get_question_type_category(self, question_type: str) -> str:
        """
        Map LongMemEval question types to readable categories

        Args:
            question_type: Raw question type string

        Returns:
            Human-readable category name
        """
        type_mapping = {
            'single-session-user': 'Single Session (User)',
            'single-session-assistant': 'Single Session (Assistant)',
            'multi-session-user': 'Multi Session (User)',
            'multi-session-assistant': 'Multi Session (Assistant)',
            'temporal-reasoning': 'Temporal Reasoning',
            'knowledge-update': 'Knowledge Update',
            'single-session-preference': 'Preference'
        }
        return type_mapping.get(question_type, question_type)
