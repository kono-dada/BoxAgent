"""
Memory components for Jev-Mem: System-One Controlled Agentic Memory.

This module provides the Temporal Resonance Graph Memory implementation.
"""

# Keep public imports compatible without initializing API clients, downloading
# metrics models, or importing benchmark dependencies when using graph storage.
from importlib import import_module
import os
import sys

# Set defaults before torch, FAISS or sklearn load native OpenMP runtimes.
# Preserve explicit tuning on installations with compatible libraries.
if sys.platform == "darwin":
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")

_MODULES = {
    "graph_db": "GraphDBInterface NetworkXGraphDB EventNode Link NodeType LinkType LinkSubType LinkStatus TraversalConstraints",
    "vector_db": "VectorDBInterface FAISSVectorDB NumpyVectorDB VectorEncoder VectorEntry IndexType create_vector_db",
    "trg_memory": "TemporalResonanceGraphMemory EventExtractionResult QueryContext",
    "episode_segmenter": "EpisodeSegmenter Episode MessageBuffer BoundaryDetector",
    "temporal_parser": "TemporalParser", "answer_formatter": "AnswerFormatter",
    "llm_judge": "LLMJudge", "memory_builder": "MemoryBuilder", "query_engine": "QueryEngine",
    "evaluation_harness": "TestHarness", "evaluator": "Evaluator",
    "jev_mem_config": "JevMemConfig", "jev_client": "JevClient",
}


def __getattr__(name):
    for module, names in _MODULES.items():
        if name in names.split():
            value = getattr(import_module("." + module, __name__), name)
            globals()[name] = value
            return value
    raise AttributeError(name)

__all__ = [
    'GraphDBInterface',
    'NetworkXGraphDB',
    'EventNode',
    'Link',
    'NodeType',
    'LinkType',
    'LinkSubType',
    'LinkStatus',
    'TraversalConstraints',

    'VectorDBInterface',
    'FAISSVectorDB',
    'NumpyVectorDB',
    'VectorEncoder',
    'VectorEntry',
    'IndexType',
    'create_vector_db',

    'TemporalResonanceGraphMemory',
    'EventExtractionResult',
    'QueryContext',

    'EpisodeSegmenter',
    'Episode',
    'MessageBuffer',
    'BoundaryDetector',

    'TemporalParser',
    'AnswerFormatter',
    'LLMJudge',

    'MemoryBuilder',
    'QueryEngine',
    'TestHarness',
    'Evaluator',
    'JevMemConfig',
    'JevClient'
]

__version__ = '0.1.0'
