"""
LongMemEval Dataset Loader

Loads the LongMemEval dataset which has a different structure than LoCoMo:
- Each question is self-contained with its own haystack sessions
- Questions have types like 'single-session-user', 'temporal-reasoning', etc.
- Sessions are embedded within each question
"""

import json
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

@dataclass
class LongMemMessage:
    """A single message in a session"""
    role: str
    content: str

@dataclass
class LongMemSession:
    """A single session with timestamp"""
    session_id: str
    date: str
    messages: List[LongMemMessage]

@dataclass
class LongMemQuestion:
    """A single question from LongMemEval dataset"""
    question_id: str
    question_type: str
    question: str
    question_date: str
    answer: str
    answer_session_ids: List[str]
    haystack_dates: List[str]
    haystack_session_ids: List[str]
    haystack_sessions: List[LongMemSession]

def parse_session(session_data: List[dict], session_id: str, date: str) -> LongMemSession:
    """Parse a single session's data"""
    messages = []
    for turn in session_data:
        messages.append(LongMemMessage(
            role=turn.get("role", "user"),
            content=turn.get("content", "")
        ))
    return LongMemSession(
        session_id=session_id,
        date=date,
        messages=messages
    )

def load_longmemeval_dataset(file_path: str, start_idx: int = 0, end_idx: Optional[int] = None) -> List[LongMemQuestion]:
    """
    Load the LongMemEval dataset from a JSON file.

    Args:
        file_path: Path to the JSON file containing the dataset
        start_idx: Start index for loading questions
        end_idx: End index for loading questions (None = load all)

    Returns:
        List of LongMemQuestion objects containing the parsed data
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Dataset file not found at {file_path}")

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if end_idx is None:
        end_idx = len(data)
    data = data[start_idx:end_idx]

    questions = []
    question_type_counts = {}
    total_sessions = 0
    total_messages = 0

    for item in data:
        try:
            haystack_sessions = []
            for session_data, session_id, date in zip(
                item['haystack_sessions'],
                item['haystack_session_ids'],
                item['haystack_dates']
            ):
                session = parse_session(session_data, session_id, date)
                haystack_sessions.append(session)
                total_messages += len(session.messages)

            total_sessions += len(haystack_sessions)

            question = LongMemQuestion(
                question_id=item['question_id'],
                question_type=item['question_type'],
                question=item['question'],
                question_date=item['question_date'],
                answer=item['answer'],
                answer_session_ids=item.get('answer_session_ids', []),
                haystack_dates=item['haystack_dates'],
                haystack_session_ids=item['haystack_session_ids'],
                haystack_sessions=haystack_sessions
            )
            questions.append(question)

            qtype = item['question_type']
            question_type_counts[qtype] = question_type_counts.get(qtype, 0) + 1

        except Exception as e:
            print(f"Error processing question {item.get('question_id', 'unknown')}: {e}")
            raise e

    print(f"\nLongMemEval Dataset Statistics:")
    print(f"  Total questions loaded: {len(questions)}")
    print(f"  Total sessions: {total_sessions}")
    print(f"  Total messages: {total_messages}")
    print(f"  Average sessions per question: {total_sessions/len(questions):.1f}")
    print(f"  Average messages per question: {total_messages/len(questions):.1f}")
    print(f"\nQuestion types:")
    for qtype, count in sorted(question_type_counts.items()):
        print(f"    {qtype}: {count}")

    return questions

def get_dataset_statistics(questions: List[LongMemQuestion]) -> Dict:
    """
    Get basic statistics about the dataset.

    Args:
        questions: List of LongMemQuestion objects

    Returns:
        Dictionary containing various statistics
    """
    question_type_counts = {}
    total_sessions = 0
    total_messages = 0

    for q in questions:
        question_type_counts[q.question_type] = question_type_counts.get(q.question_type, 0) + 1
        total_sessions += len(q.haystack_sessions)
        for session in q.haystack_sessions:
            total_messages += len(session.messages)

    return {
        "num_questions": len(questions),
        "question_types": question_type_counts,
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "avg_sessions_per_question": total_sessions / len(questions) if questions else 0,
        "avg_messages_per_question": total_messages / len(questions) if questions else 0
    }

def main():
    dataset_path = Path("data") / "longmemeval_s_cleaned.json"
    try:
        print(f"Loading dataset from: {dataset_path}")
        questions = load_longmemeval_dataset(dataset_path, start_idx=0, end_idx=5)

        print(f"\nFirst question details:")
        q = questions[0]
        print(f"  ID: {q.question_id}")
        print(f"  Type: {q.question_type}")
        print(f"  Question: {q.question}")
        print(f"  Answer: {q.answer}")
        print(f"  Date: {q.question_date}")
        print(f"  Sessions: {len(q.haystack_sessions)}")

    except Exception as e:
        print(f"Error loading dataset: {e}")
        raise


if __name__ == "__main__":
    main()
