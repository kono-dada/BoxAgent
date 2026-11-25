"""Load local Core ML image models for Qwen vision inputs."""

import ctypes
import hashlib
import json
import os
import platform
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

PATCH_WIDTH = 1536
FEATURE_WIDTH = 1024
REVIEW_GRID = (56, 64)
SIX_FRAME_SHEET_COMMENT = b"BoxAgent six-frame contact sheet v1"
SOURCE_FILES = ("model.safetensors", "config.json")


def source_fingerprints(model_path: Path) -> dict[str, str]:
    values = {}
    for name in SOURCE_FILES:
        with (model_path / name).open("rb") as source:
            values[name] = hashlib.file_digest(source, "sha256").hexdigest()
    return values


class MaskedImageEncoder:
    """Add masked padding to a compiled patch capacity, preserving the original grid."""

    def __init__(self, bundle: Path, fingerprints: dict[str, str], tower: Any) -> None:
        from wuwa_text_prefill import TensorBuffer

        if sys.platform != "darwin" or platform.machine() != "arm64":
            raise RuntimeError("Core ML image encoding requires an Apple Silicon Mac")
        self.root = bundle / "vision-masked"
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or manifest.get("version") != 1
                or manifest.get("source_sha256") != fingerprints):
            raise ValueError("The masked vision models do not match the Qwen source")
        capacities = manifest.get("capacities")
        if (not isinstance(capacities, list) or not capacities
                or any(type(n) is not int or n <= 0 or n % 4 for n in capacities)):
            raise ValueError("Invalid masked vision patch capacities")
        self.capacities = sorted(set(capacities))
        library = ctypes.CDLL(str(bundle / "text" / "libwuwa_text.dylib"))
        self.open = library.boxagent_wuwa_text_open
        self.open.argtypes = (ctypes.c_char_p,)
        self.open.restype = ctypes.c_void_p
        self.predict = library.boxagent_wuwa_text_predict
        self.predict.argtypes = (
            ctypes.c_void_p, ctypes.POINTER(TensorBuffer), ctypes.c_size_t,
            ctypes.POINTER(TensorBuffer), ctypes.c_size_t,
        )
        self.predict.restype = ctypes.c_int
        self.release = library.boxagent_wuwa_text_close
        self.release.argtypes = (ctypes.c_void_p,)
        self.release.restype = None
        self.handle: int | None = None
        self.capacity = 0
        self.failed: set[int] = set()
        self.tower = tower
        self.geometry = lru_cache(maxsize=4)(self._geometry)
        self.enabled = False

    def prepare_image(self, image: str, *, pressured: bool) -> None:
        from PIL import Image

        self.enabled = False
        if not pressured:
            return
        try:
            with Image.open(image) as source:
                self.enabled = source.info.get("comment") != SIX_FRAME_SHEET_COMMENT
        except OSError:
            # The regular image reader reports malformed inputs.
            pass

    def _geometry(self, height: int, width: int, capacity: int) -> dict[str, Any]:
        import mlx.core as mx
        import numpy as np

        count = height * width
        with mx.stream(mx.cpu):
            grid = mx.array([[1, height, width]], dtype=mx.int32)
            embedding = self.tower.fast_pos_embed_interpolate(grid)
            position = np.zeros((1, embedding.shape[-1], 1, capacity), dtype=np.float16)
            position[0, :, 0, :count] = np.asarray(embedding.astype(mx.float16)).T
            angles = self.tower.rot_pos_emb(grid)
            angles = mx.concatenate([angles, angles], axis=-1)
            cosine, sine = (
                np.zeros((1, 1, angles.shape[-1], capacity), dtype=np.float16) for _ in range(2)
            )
            cosine[..., :count] = np.asarray(mx.cos(angles).astype(mx.float16)).T
            sine[..., :count] = np.asarray(mx.sin(angles).astype(mx.float16)).T
        key_bias = np.full((1, capacity, 1, 1), -10000, dtype=np.float16)
        key_bias[:, :count] = 0
        return {"position": position, "cosine": cosine, "sine": sine, "key_bias": key_bias}

    def __call__(self, pixel_values: Any, height: int, width: int) -> Any | None:
        # Use the original global vision attention for time-ordered evidence.
        if not self.enabled or (height, width) == REVIEW_GRID:
            return None
        import mlx.core as mx
        import numpy as np
        from wuwa_text_prefill import buffers

        count = height * width
        capacity = next((n for n in self.capacities if n >= count and n not in self.failed), None)
        if capacity is None or pixel_values.shape != (count, PATCH_WIDTH):
            return None
        try:
            if capacity != self.capacity or self.handle is None:
                self._release_model()
                filename = self.root / str(capacity) / "vision.mlmodelc"
                self.handle = self.open(os.fsencode(filename))
                if not self.handle:
                    raise RuntimeError("Core ML rejected the masked image model")
                self.capacity = capacity
            with mx.stream(mx.cpu):
                pixels = np.zeros((1, PATCH_WIDTH, 1, capacity), dtype=np.float16)
                half = pixel_values.astype(mx.bfloat16).astype(mx.float16)
                pixels[0, :, 0, :count] = np.asarray(half).T
                inputs = {"pixels": pixels, **self.geometry(height, width, capacity)}
                features = np.empty((capacity // 4, FEATURE_WIDTH), dtype=np.float16)
                result = self.predict(
                    self.handle, buffers(inputs), len(inputs), buffers({"out": features}), 1,
                )
                if result != 0 or not np.isfinite(features[:count // 4]).all():
                    raise RuntimeError("Core ML masked image encoding failed")
                return mx.array(features[:count // 4]).astype(mx.bfloat16)
        except (OSError, RuntimeError, ValueError) as exc:
            self.failed.add(capacity)
            self._release_model()
            print(f"Vision Core ML capacity {capacity} unavailable; using MLX: {exc}",
                  file=sys.stderr)
            return None

    def _release_model(self) -> None:
        if self.handle:
            self.release(self.handle)
            self.handle = None

    def close(self) -> None:
        self.enabled = False
        self.geometry.cache_clear()
        self._release_model()
