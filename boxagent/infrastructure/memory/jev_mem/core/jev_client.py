"""Central Jev SDK adapter for mixed Noul/Choice decisions.

The outer retry loop owns Jev-Mem's call/deadline budget; SDK retries are disabled.
Protocol: https://docs.typesafe.ai/api
"""

from collections import OrderedDict
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field, replace
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import random
import threading
import time
from typing import Dict

from typesafe_sdk import (
    Choice, ChoiceAnswer, Noul, RetryPolicy, TypeSafeClient,
    TypeSafeAPIConnectionError, TypeSafeAPIError, TypeSafeAPIResponseValidationError,
)

from .jev_mem_config import JevMemConfig

logger = logging.getLogger("jev_mem")


class JevUnavailable(RuntimeError):
    pass


@dataclass
class CallBudget:
    maximum_calls: int
    deadline: float
    calls: int = 0
    cache_hits: int = 0
    fallback_events: list = field(default_factory=list)

    def remaining_seconds(self):
        return max(0.0, self.deadline - time.monotonic())

    def consume(self):
        if self.calls >= self.maximum_calls or self.remaining_seconds() <= 0:
            raise JevUnavailable("budget_exhausted")
        self.calls += 1


@dataclass(frozen=True)
class ProbabilityResult:
    """Noul probabilities and complete Choice answers, never conflated."""
    values: Dict[str, float]
    source: str
    choices: Dict[str, ChoiceAnswer] = field(default_factory=dict)
    model: str = ""
    usage: dict = field(default_factory=dict)


class DecisionLog:
    def __init__(self, path=""):
        self.path = Path(path) if path else None
        self.lock = threading.Lock()

    def emit(self, event, **values):
        record = {"project": "Jev-Mem", "event": event, **values}
        line = json.dumps(record, sort_keys=True, allow_nan=False)
        logger.info(line)
        if self.path:
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as stream:
                    stream.write(line + "\n")


class JevClient:
    def __init__(self, config=None, api_key=None, transport=None, mock=None, audit=None):
        self.config = config or JevMemConfig.load()
        # CLI entry points load .env; the SDK owns Bearer authentication.
        # An explicit key takes precedence, including an empty key (fail closed).
        self.api_key = (api_key if api_key is not None else os.getenv("TYPESAFE_API_KEY", "")).strip()
        self.transport = transport
        self.mock = mock
        self.audit = audit or DecisionLog(self.config.audit_path)
        self.cache = OrderedDict()
        self.lock = threading.Lock()
        self._retrieval_lock = threading.Lock()
        self._sdk = None
        self._laya = None
        if self.config.decision_backend in ("laya", "laya-mlx") and not self.config.jev_mock:
            from .laya_backend import LayaBackend
            self._laya = LayaBackend(self.config)

    def _get_sdk(self):
        with self.lock:
            if self._sdk is None:
                self._sdk = TypeSafeClient(
                    api_key=self.api_key, model=self.config.jev_model,
                    base_url=self.config.jev_base_url, transport=self.transport,
                    retry=RetryPolicy(max_retries=0, timeout=None),
                )
            return self._sdk

    def retrieval_slot(self):
        """Queue local retrievals before their deadline starts.

        Laya already serializes model calls. Let one retrieval finish its calls
        instead of making several query deadlines expire in that model queue.
        Remote Jev and deterministic fixtures retain parallel retrieval.
        """
        if self.config.decision_backend in ("laya", "laya-mlx") and not self.config.jev_mock:
            return self._retrieval_lock
        return nullcontext()

    def close(self):
        if self._laya is not None:
            self._laya.close()
        with self.lock:
            if self._sdk is not None:
                self._sdk.close()
                self._sdk = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def probabilities(self, operation, state, questions, *, mock_values=None, budget=None):
        """Compatibility helper for Noul-only callers, including plain strings."""
        questions = {k: Noul(instructions=q) if isinstance(q, str) else q for k, q in questions.items()}
        if any(not isinstance(q, Noul) for q in questions.values()):
            raise ValueError("Use evaluate() for mixed Noul/Choice questions")
        return self.evaluate(operation, state, questions, mock_values=mock_values, budget=budget)

    def evaluate(self, operation, state, questions, *, mock_values=None, budget=None):
        """Ask a batch of independent typed questions against shared state.

        Returns None on configured fallback. Choice results retain their selected
        option, complete distribution and confidence. No answer in this batch is
        used as hidden context for another question.
        """
        wire = self._question_payload(questions)
        model = (self.config.laya_model if self.config.decision_backend in ("laya", "laya-mlx")
                 else self.config.jev_model)
        payload = {"backend": self.config.decision_backend, "model": model,
                   "subfolder": self.config.laya_subfolder, "state": state, "questions": wire}
        key = hashlib.sha256(json.dumps({"operation": operation, **payload}, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if budget is None:
            budget = CallBudget(self.config.max_retries + 1,
                                time.monotonic() + self.config.timeout_seconds * (self.config.max_retries + 1))
        if budget.remaining_seconds() <= 0:
            return self._failure(operation, "deadline", budget)
        with self.lock:
            cached = self.cache.get(key)
            if cached is not None:
                self.cache.move_to_end(key)
                cached = deepcopy(cached)
        if cached is not None:
            budget.cache_hits += 1
            cached = replace(cached, source="cache", usage={})
            self._log(operation, cached)
            return cached
        error = "unavailable"
        for attempt in range(self.config.max_retries + 1):
            retry_after = None
            try:
                if not self.config.jev_mock and self.config.decision_backend == "jev" and not self.api_key:
                    raise JevUnavailable("missing_typesafe_api_key")
                budget.consume()
                if self.config.jev_mock:
                    values = self.mock(operation, state, questions) if self.mock else mock_values
                    raw = {"model": model, "usage": {}, "answers": {}}
                    for name, question in questions.items():
                        value = (values or {})[name]
                        raw["answers"][name] = ({"type": "noul", "noul": value} if isinstance(question, Noul)
                                                else value.model_dump() if isinstance(value, ChoiceAnswer) else value)
                elif self.config.decision_backend in ("laya", "laya-mlx"):
                    timeout = min(self.config.timeout_seconds, budget.remaining_seconds())
                    try:
                        raw = self._laya.predict(state, wire, timeout)
                    except TimeoutError:
                        raise JevUnavailable("laya_deadline") from None
                    except (OSError, RuntimeError):
                        raise JevUnavailable("laya_inference_failed") from None
                else:
                    timeout = min(self.config.timeout_seconds, budget.remaining_seconds())
                    response = self._get_sdk().system_one(state=state, questions=questions, timeout=timeout)
                    raw = response.model_dump()
                values, choices = self._validate(raw, questions)
                if budget.remaining_seconds() <= 0:
                    raise JevUnavailable("deadline")
                result = ProbabilityResult(values, "mock" if self.config.jev_mock else self.config.decision_backend,
                                           choices, raw.get("model", model), raw.get("usage", {}))
                if self.config.cache_size:
                    with self.lock:
                        self.cache[key] = deepcopy(result)
                        self.cache.move_to_end(key)
                        while len(self.cache) > self.config.cache_size:
                            self.cache.popitem(last=False)
                self._log(operation, result)
                return result
            except JevUnavailable as exc:
                error = str(exc)
                break
            except TypeSafeAPIResponseValidationError:
                error = "invalid_response"
            except TypeSafeAPIError as exc:
                error = "http_" + str(exc.status)
                if exc.status not in (408, 429) and exc.status < 500:
                    break
                retry_after = self._retry_after(exc.headers)
            except (TypeSafeAPIConnectionError, ValueError, KeyError, TypeError) as exc:
                # No response bodies, request state, or credentials in failure logs.
                error = type(exc).__name__
            if attempt < self.config.max_retries and budget.calls < budget.maximum_calls:
                delay = retry_after if retry_after is not None else min(5.0, 0.5 * 2 ** attempt) * random.uniform(0.75, 1.0)
                if delay >= budget.remaining_seconds():
                    break  # Do not sleep through the deadline or violate Retry-After.
                time.sleep(delay)
        return self._failure(operation, error, budget)

    @staticmethod
    def _question_payload(questions):
        if not questions:
            raise ValueError("At least one question is required")
        wire = {}
        for name, question in questions.items():
            if not isinstance(name, str) or not name or not isinstance(question, (Noul, Choice)):
                raise ValueError("Questions must have IDs and typed Noul/Choice definitions")
            if not question.instructions:
                raise ValueError("Each question needs explicit instructions; IDs are not sent to the model")
            if isinstance(question, Choice) and len(question.criteria) < 2:
                raise ValueError("Choice requires at least two options")
            wire[name] = question.model_dump()
        return wire

    @staticmethod
    def _probability(value):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Invalid probability")
        return float(value)

    @classmethod
    def _validate(cls, raw, questions):
        answers = raw["answers"]
        values, choices = {}, {}
        for name, question in questions.items():
            item = answers[name]
            if isinstance(question, (Noul, str)):
                if item["type"] != "noul":
                    raise ValueError("Unexpected answer type")
                values[name] = cls._probability(item["noul"])
            else:
                if item["type"] != "choice" or item["choice"] not in question.criteria:
                    raise ValueError("Unexpected Choice answer")
                probs = item["probabilities"]
                if set(probs) != set(question.criteria):
                    raise ValueError("Choice distribution must cover exactly the requested options")
                probs = {k: cls._probability(v) for k, v in probs.items()}
                if not math.isclose(sum(probs.values()), 1.0, abs_tol=1e-3):
                    raise ValueError("Choice distribution must sum to one")
                if probs[item["choice"]] + 1e-6 < max(probs.values()):
                    raise ValueError("Selected choice is not a highest-probability option")
                choices[name] = ChoiceAnswer(choice=item["choice"], probabilities=probs,
                                             confidence=cls._probability(item["confidence"]))
        return values, choices

    @staticmethod
    def _retry_after(headers):
        for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
            value = headers.get(name)
            if value is None:
                continue
            try:
                seconds = float(value) * scale
            except ValueError:
                try:
                    seconds = parsedate_to_datetime(value).timestamp() - time.time()
                except (ValueError, TypeError, OverflowError):
                    continue
            if math.isfinite(seconds) and seconds >= 0:
                return seconds
        return None

    def _log(self, operation, result):
        self.audit.emit("jev_decision", operation=operation, source=result.source, model=result.model,
                        decisions=result.values, choices={k: v.model_dump() for k, v in result.choices.items()},
                        usage=result.usage)

    def _failure(self, operation, reason, budget):
        budget.fallback_events.append({"operation": operation, "reason": reason})
        self.audit.emit("jev_fallback", operation=operation, reason=reason)
        if not self.config.fallback_to_magma:
            raise JevUnavailable(operation + ": " + reason)
        return None
