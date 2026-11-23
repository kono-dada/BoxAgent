"""Vendored Jev-Mem runtime and BoxAgent process adapter.

The heavy Jev dependencies remain isolated in the configured memory Python
environment.  Product code imports only :class:`JevMemWorker`.
"""

from boxagent.infrastructure.memory.jev_mem.client import JevMemWorker

__all__ = ["JevMemWorker"]
