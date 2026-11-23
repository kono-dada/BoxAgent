"""Read older cache names while writing only the current Jev-Mem schema.

Historical project names belong here, not in the public API or new outputs.
"""
from pathlib import Path


def memory_config_path(directory):
    """Prefer the current configuration filename when both versions exist."""
    directory = Path(directory)
    current = directory / "jev_mem_config.json"
    return current if current.exists() else directory / "sys1mem_config.json"


def validate_cached_backend(directory, config):
    """Reject graphs built with a different decision backend before loading them."""
    import json

    path = memory_config_path(directory)
    saved = json.loads(path.read_text()) if path.exists() else {}
    # Historical caches predate local backends and used Jev.
    old_backend = saved.get("decision_backend", "jev")
    if old_backend != config.decision_backend:
        raise ValueError("Reuse cache has different construction settings: decision_backend")
    if config.decision_backend in ("laya", "laya-mlx"):
        defaults = {"laya_model": "convaiinnovations/laya", "laya_subfolder": "", "jev_mock": False}
        changed = [key for key, default in defaults.items()
                   if saved.get(key, default) != getattr(config, key)]
        if changed:
            raise ValueError("Reuse cache has different construction settings: " + ", ".join(changed))


def normalize_metadata(metadata):
    """Copy metadata and migrate historical controller keys without data loss."""
    result = dict(metadata)
    legacy = result.pop("sys1mem", None)
    if legacy is not None:
        # Explicit current-format fields take precedence in a mixed cache.
        result["jev_mem"] = {**legacy, **result.get("jev_mem", {})}
    if result.get("source") == "sys1mem_consolidation":
        result["source"] = "jev_mem_consolidation"
    if result.get("controller") in ("sys1mem", "sys1-mem"):
        result["controller"] = "jev-mem"
    return result
