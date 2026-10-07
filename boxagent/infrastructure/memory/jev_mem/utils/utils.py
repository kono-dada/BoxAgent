import re
from boxagent.infrastructure.memory.jev_mem.core.local_device import local_model_device
import string
import numpy as np
from typing import List, Dict, Union, Optional
import statistics
from collections import defaultdict
import logging
from dataclasses import dataclass
from pathlib import Path
import threading

# Optional imports - will fallback gracefully if not available
try:
    from rouge_score import rouge_scorer
    ROUGE_AVAILABLE = True
except ImportError:
    ROUGE_AVAILABLE = False
    logging.warning("rouge_score not available, ROUGE metrics disabled")

try:
    from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
    import nltk
    from nltk.translate.meteor_score import meteor_score
    NLTK_AVAILABLE = True
except ImportError:
    NLTK_AVAILABLE = False
    logging.warning("NLTK not available, BLEU/METEOR metrics disabled")

try:
    from bert_score import score as bert_score
    BERT_SCORE_AVAILABLE = True
except ImportError:
    BERT_SCORE_AVAILABLE = False
    logging.warning("bert_score not available, BERTScore metrics disabled")

try:
    from sentence_transformers import SentenceTransformer
    from sentence_transformers.util import pytorch_cos_sim
    SENTENCE_TRANSFORMER_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMER_AVAILABLE = False
    logging.warning("sentence_transformers not available, semantic similarity disabled")

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logging.warning("OpenAI not available")

try:
    from boxagent.infrastructure.memory.jev_mem.api.datasets.locomo import load_locomo_dataset, QA, Turn, Session, Conversation
except ImportError:
    logging.warning("load_dataset not available")

sentence_model = None
_sentence_model_initialized = False
_nltk_initialized = False
_resource_lock = threading.Lock()
bert_score_lock = threading.Lock()


def _initialize_nltk():
    """Prepare optional scoring data only when a metric is requested."""
    global _nltk_initialized
    if not NLTK_AVAILABLE:
        return
    with _resource_lock:
        if not _nltk_initialized:
            try:
                for resource in ('punkt', 'punkt_tab', 'wordnet'):
                    nltk.download(resource, quiet=True)
            except Exception as exc:
                logging.warning("Could not prepare optional NLTK scoring data: %s", exc)
            _nltk_initialized = True


def _get_sentence_model():
    """Load optional semantic scoring weights lazily, once per process."""
    global sentence_model, _sentence_model_initialized
    if not SENTENCE_TRANSFORMER_AVAILABLE:
        return None
    with _resource_lock:
        if not _sentence_model_initialized:
            try:
                sentence_model = SentenceTransformer('all-MiniLM-L6-v2', device=local_model_device())
            except Exception as exc:
                logging.warning("Could not load optional semantic scoring model: %s", exc)
            _sentence_model_initialized = True
    return sentence_model


def simple_tokenize(text):
    """Simple tokenization function."""
    # Convert to string if not already
    text = str(text)
    return text.lower().replace('.', ' ').replace(',', ' ').replace('!', ' ').replace('?', ' ').split()

def calculate_rouge_scores(prediction: str, reference: str) -> Dict[str, float]:
    """Calculate ROUGE scores for prediction against reference."""
    if not ROUGE_AVAILABLE:
        # Return zeros if rouge_score is not available
        return {
            'rouge1_f': 0.0,
            'rouge2_f': 0.0,
            'rougeL_f': 0.0
        }

    scorer = rouge_scorer.RougeScorer(['rouge1', 'rouge2', 'rougeL'], use_stemmer=True)
    scores = scorer.score(reference, prediction)
    return {
        'rouge1_f': scores['rouge1'].fmeasure,
        'rouge2_f': scores['rouge2'].fmeasure,
        'rougeL_f': scores['rougeL'].fmeasure
    }

def calculate_bleu_scores(prediction: str, reference: str) -> Dict[str, float]:
    """Calculate BLEU scores with different n-gram settings."""
    if not NLTK_AVAILABLE:
        return {'bleu': 0.0, 'bleu1': 0.0, 'bleu2': 0.0, 'bleu4': 0.0}

    _initialize_nltk()
    pred_tokens = nltk.word_tokenize(prediction.lower())
    ref_tokens = [nltk.word_tokenize(reference.lower())]

    weights_list = [(1, 0, 0, 0), (0.5, 0.5, 0, 0), (0.33, 0.33, 0.33, 0), (0.25, 0.25, 0.25, 0.25)]
    smooth = SmoothingFunction().method1

    scores = {}
    for n, weights in enumerate(weights_list, start=1):
        try:
            score = sentence_bleu(ref_tokens, pred_tokens, weights=weights, smoothing_function=smooth)
        except Exception:
            score = 0.0
        scores[f'bleu{n}'] = score

    return scores

def calculate_bert_scores(prediction: str, reference: str) -> Dict[str, float]:
    """Calculate BERTScore for semantic similarity.

    Thread-safe: Uses lock to prevent concurrent PyTorch model access.
    """
    try:
        # Use lock to prevent multiple threads from loading PyTorch model simultaneously
        with bert_score_lock:
            P, R, F1 = bert_score([prediction], [reference], lang='en', verbose=False,
                                 device=local_model_device())
        return {
            'bert_precision': P.item(),
            'bert_recall': R.item(),
            'bert_f1': F1.item()
        }
    except Exception as e:
        # Silently return zeros if BERTScore is not available
        return {
            'bert_precision': 0.0,
            'bert_recall': 0.0,
            'bert_f1': 0.0
        }

def calculate_meteor_score(prediction: str, reference: str) -> float:
    """Calculate METEOR score for the prediction."""
    try:
        _initialize_nltk()
        return meteor_score([reference.split()], prediction.split())
    except Exception as e:
        # Silently return 0 if METEOR is not available
        return 0.0

def calculate_sentence_similarity(prediction: str, reference: str) -> float:
    """Calculate sentence embedding similarity using SentenceBERT."""
    model = _get_sentence_model()
    if model is None:
        return 0.0
    try:
        # Encode sentences
        embedding1 = model.encode([prediction], convert_to_tensor=True)
        embedding2 = model.encode([reference], convert_to_tensor=True)

        # Calculate cosine similarity
        similarity = pytorch_cos_sim(embedding1, embedding2).item()
        return float(similarity)
    except Exception as e:
        # Silently return 0 if sentence similarity calculation fails
        return 0.0

def is_unanswerable(text: str) -> bool:
    """
    Check if a text represents an 'unanswerable' response.

    Args:
        text: Text to check

    Returns:
        True if text indicates unanswerable/no information
    """
    if not text:
        return True

    text_lower = text.strip().lower()

    # Empty or null-like values
    if text_lower in {"", "n/a", "na", "none", "null", "unanswerable"}:
        return True

    # Common patterns indicating no information
    patterns = [
        "not mentioned",
        "not in the conversation",
        "cannot answer",
        "can't answer",
        "insufficient",
        "unknown",
        "no information",
        "not provided",
        "information not found",
        "not found",
        "not available",
        "no data"
    ]

    return any(pattern in text_lower for pattern in patterns)

def calculate_metrics(prediction: str, reference: str, category: Optional[int] = None) -> Dict[str, float]:
    """
    Calculate comprehensive evaluation metrics for a prediction.

    Args:
        prediction: Predicted answer
        reference: Reference (gold) answer
        category: Question category (1-5), used for special handling of adversarial questions

    Returns:
        Dictionary of metric scores
    """
    # Special handling for Category 5 (adversarial questions)
    # For Category 5, IGNORE the expected answer - only check if prediction is "unanswerable"
    # These are adversarial questions where the info is NOT in the conversation
    if category == 5:
        pred_is_unans = is_unanswerable(prediction)

        # Predicted unanswerable -> CORRECT (perfect scores)
        # This is the right behavior for adversarial questions
        if pred_is_unans:
            return {
                "exact_match": 1,
                "f1": 1.0,
                "rouge1_f": 1.0,
                "rouge2_f": 1.0,
                "rougeL_f": 1.0,
                "bleu1": 1.0,
                "bleu2": 1.0,
                "bleu3": 1.0,
                "bleu4": 1.0,
                "bert_f1": 1.0,
                "meteor": 1.0,
                "sbert_similarity": 1.0
            }
        # Predicted concrete answer -> WRONG (zero scores)
        # This is hallucination - answering when should say "not found"
        else:
            return {
                "exact_match": 0,
                "f1": 0.0,
                "rouge1_f": 0.0,
                "rouge2_f": 0.0,
                "rougeL_f": 0.0,
                "bleu1": 0.0,
                "bleu2": 0.0,
                "bleu3": 0.0,
                "bleu4": 0.0,
                "bert_f1": 0.0,
                "meteor": 0.0,
                "sbert_similarity": 0.0
            }

    # Handle empty or None values
    if not prediction or not reference:
        return {
            "exact_match": 0,
            "f1": 0.0,
            "rouge1_f": 0.0,
            "rouge2_f": 0.0,
            "rougeL_f": 0.0,
            "bleu1": 0.0,
            "bleu2": 0.0,
            "bleu3": 0.0,
            "bleu4": 0.0,
            "bert_f1": 0.0,
            "meteor": 0.0,
            "sbert_similarity": 0.0
        }

    # Convert to strings if they're not already
    prediction = str(prediction).strip()
    reference = str(reference).strip()

    # Calculate exact match
    exact_match = int(prediction.lower() == reference.lower())

    # Calculate token-based F1 score
    pred_tokens = set(simple_tokenize(prediction))
    ref_tokens = set(simple_tokenize(reference))
    common_tokens = pred_tokens & ref_tokens

    if not pred_tokens or not ref_tokens:
        f1 = 0.0
    else:
        precision = len(common_tokens) / len(pred_tokens)
        recall = len(common_tokens) / len(ref_tokens)
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # Calculate all scores
    rouge_scores = calculate_rouge_scores(prediction, reference)
    bleu_scores = calculate_bleu_scores(prediction, reference)
    bert_scores = calculate_bert_scores(prediction, reference)
    meteor = calculate_meteor_score(prediction, reference)
    sbert_similarity = calculate_sentence_similarity(prediction, reference)

    # Combine all metrics
    metrics = {
        "exact_match": exact_match,
        "f1": f1,
        **rouge_scores,
        **bleu_scores,
        **bert_scores,
        "meteor": meteor,
        "sbert_similarity": sbert_similarity
    }

    return metrics

def aggregate_metrics(all_metrics: List[Dict[str, float]], all_categories: List[int]) -> Dict[str, Dict[str, Union[float, Dict[str, float]]]]:
    """Calculate aggregate statistics for all metrics, split by category."""
    if not all_metrics:
        return {}

    aggregates = defaultdict(list)
    category_aggregates = defaultdict(lambda: defaultdict(list))

    for metrics, category in zip(all_metrics, all_categories):
        for metric_name, value in metrics.items():
            aggregates[metric_name].append(value)
            category_aggregates[category][metric_name].append(value)

    results = {
        "overall": {}
    }

    for metric_name, values in aggregates.items():
        results["overall"][metric_name] = {
            'mean': statistics.mean(values),
            'std': statistics.stdev(values) if len(values) > 1 else 0.0,
            'median': statistics.median(values),
            'min': min(values),
            'max': max(values),
            'count': len(values)
        }

    for category in sorted(category_aggregates.keys()):
        results[f"category_{category}"] = {}
        for metric_name, values in category_aggregates[category].items():
            if values:
                results[f"category_{category}"][metric_name] = {
                    'mean': statistics.mean(values),
                    'std': statistics.stdev(values) if len(values) > 1 else 0.0,
                    'median': statistics.median(values),
                    'min': min(values),
                    'max': max(values),
                    'count': len(values)
                }

    return results
