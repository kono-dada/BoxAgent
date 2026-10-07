"""Public Jev-Mem API, loaded lazily to avoid initializing model providers."""
from importlib import import_module

__version__ = "0.1.0"
__all__ = ["JevMemSystem", "JevMemConfig", "MemoryBuilder", "QueryEngine"]

_EXPORTS = {
    "JevMemSystem": "boxagent.infrastructure.memory.jev_mem.api.system",
    "JevMemConfig": "boxagent.infrastructure.memory.jev_mem.core.jev_mem_config",
    "MemoryBuilder": "boxagent.infrastructure.memory.jev_mem.core.memory_builder",
    "QueryEngine": "boxagent.infrastructure.memory.jev_mem.core.query_engine",
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module(_EXPORTS[name]), name)
    globals()[name] = value
    return value
