"""Shared device policy for local inference in parallel query workers."""

import sys


def local_model_device(requested=None):
    """Avoid macOS Metal command-buffer aborts; keep accelerator auto elsewhere.

    An explicit device still wins for callers who have validated their runtime.
    Native Metal assertions abort Python and cannot trigger exception fallback.
    """
    if requested not in (None, "auto"):
        return requested
    return "cpu" if sys.platform == "darwin" else None
