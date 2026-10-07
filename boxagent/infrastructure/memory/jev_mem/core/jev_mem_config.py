"""Validated, opt-in configuration for the Jev-Mem control plane."""

import json
import math
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class JevMemConfig:
    write_enabled: bool = False
    read_enabled: bool = False
    admission_enabled: bool = False
    jev_mock: bool = False
    decision_backend: str = "jev"
    laya_model: str = "convaiinnovations/laya"
    laya_subfolder: str = ""
    laya_device: str = "auto"
    laya_batch_size: int = 8
    jev_model: str = "jev-latest"
    decision_schema_version: str = "noul-choice-v3-magma-temporal"
    retrieval_schema_version: str = "anchored-temporal-v1"
    jev_base_url: str = "https://api.typesafe.ai"
    timeout_seconds: float = 3.0
    max_retries: int = 2
    cache_size: int = 1024
    fallback_to_magma: bool = True
    admission_threshold: float = 0.60
    relation_threshold: float = 0.60
    candidate_top_k: int = 10
    admission_weights: tuple = (0.4, 0.3, 0.3, 0.2)
    total_graph_budget: int = 20
    probability_exponent: float = 1.5
    minimum_graph_budget: int = 1
    graph_activation_threshold: float = 0.15
    anchor_count: int = 5
    answer_top_k: int = 15
    multihop_top_k: int = 30
    beam_width: int = 5
    maximum_depth: int = 5
    maximum_nodes: int = 30
    maximum_edges: int = 200
    maximum_jev_calls: int = 10
    max_latency_seconds: float = 15.0
    transition_weights: tuple = (0.25, 0.35, 0.15, 0.15, 0.10)
    stopping_enabled: bool = True
    evidence_sufficient_threshold: float = 0.85
    continue_threshold: float = 0.40
    consolidation_interval: int = 0
    consolidation_threshold: float = 0.85
    audit_path: str = ""

    def __post_init__(self):
        if self.decision_backend not in ("jev", "laya", "laya-mlx"):
            raise ValueError("decision_backend must be jev, laya or laya-mlx")
        for name in ("laya_model", "laya_device"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(name + " must be a nonempty string")
        if not isinstance(self.laya_subfolder, str):
            raise ValueError("laya_subfolder must be a string")
        if self.decision_backend == "laya-mlx" and self.laya_device not in ("auto", "cpu", "gpu", "metal"):
            raise ValueError("laya-mlx device must be auto, cpu, gpu or metal")
        self._integer("laya_batch_size", 1)
        for name in ("admission_threshold", "relation_threshold", "graph_activation_threshold",
                     "evidence_sufficient_threshold", "continue_threshold", "consolidation_threshold"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(name + " must be in [0, 1]")
        for name in ("candidate_top_k", "anchor_count", "answer_top_k", "multihop_top_k", "beam_width", "maximum_nodes", "maximum_edges"):
            self._integer(name, 1)
        for name in ("max_retries", "cache_size", "total_graph_budget", "minimum_graph_budget",
                     "maximum_depth", "maximum_jev_calls", "consolidation_interval"):
            self._integer(name, 0)
        for name in ("timeout_seconds", "max_latency_seconds", "probability_exponent"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(name + " must be positive and finite")
        for name, count in (("admission_weights", 4), ("transition_weights", 5)):
            weights = getattr(self, name)
            if len(weights) != count or any(not math.isfinite(w) or w < 0 for w in weights) or not sum(weights):
                raise ValueError("Invalid " + name)
        if sum(self.admission_weights[:3]) <= 0:
            raise ValueError("At least one positive admission utility weight is required")
        for name in ("write_enabled", "read_enabled", "admission_enabled", "jev_mock", "fallback_to_magma", "stopping_enabled"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(name + " must be a boolean")

    def _integer(self, name, minimum):
        value = getattr(self, name)
        if type(value) is not int or value < minimum:
            raise ValueError(name + " must be an integer >= " + str(minimum))

    @classmethod
    def load(cls, path=None, **overrides):
        values = json.loads(Path(path).read_text()) if path else {}
        allowed = {f.name for f in fields(cls)}
        if set(values) - allowed:
            raise ValueError("Unknown Jev-Mem settings: " + str(sorted(set(values) - allowed)))
        for env, setting in (("TYPESAFE_DEFAULT_MODEL", "jev_model"), ("TYPESAFE_BASE_URL", "jev_base_url")):
            if os.getenv(env):
                values[setting] = os.environ[env]
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)

    def to_dict(self):
        return asdict(self)
