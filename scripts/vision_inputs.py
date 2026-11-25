"""Prepare the pinned Qwen image patches for local visual inference."""

from __future__ import annotations

from copy import copy
from typing import TYPE_CHECKING, Any

from PIL import Image

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


class QwenImageInputs:
    def __init__(
        self, original: Any, table: NDArray[np.float32], *, cache_for_review: bool = False,
    ) -> None:
        self.original = original
        self.table = table
        self.cache_for_review = cache_for_review
        self._prepared: dict[str, Any] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.original, name)

    def __call__(self, images: Any, **options: Any) -> dict[str, Any]:
        if (
            options or not isinstance(images, list) or len(images) != 1
            or not isinstance(images[0], Image.Image) or images[0].mode != "RGB"
        ):
            return self.original(images=images, **options)
        if self._prepared is not None:
            return self._prepared
        data = self._pack(images[0])
        if self.cache_for_review:
            import mlx.core as mx

            data["pixel_values"] = mx.array(data["pixel_values"])
            self._prepared = data
        return data

    def _pack(self, image: Image.Image) -> dict[str, Any]:
        import numpy as np

        height, width = self.original._resolved_size(image.height, image.width)
        if (width, height) != image.size:
            image = image.resize((width, height), Image.Resampling.BICUBIC)
        gh, gw = height // 16, width // 16
        rgb = np.asarray(image).reshape(gh // 2, 2, 16, gw // 2, 2, 16, 3)
        # Match Qwen's spatial merge order, then duplicate each patch's temporal plane.
        ordered = self.table[rgb.transpose(0, 3, 1, 4, 6, 2, 5)]
        temporal = np.broadcast_to(ordered[..., None, :, :], (*ordered.shape[:5], 2, 16, 16))
        patches = np.ascontiguousarray(temporal).reshape(gh * gw, 1536)
        return {"pixel_values": patches, "image_grid_thw": np.array([[1, gh, gw]], dtype=np.int64)}


def enable_image_inputs(processor: Any) -> None:
    import numpy as np

    try:
        from mlx_vlm.models.qwen3_vl.processing_qwen3_vl import Qwen3VLImageProcessor
    except ImportError:
        return

    source = getattr(processor, "image_processor", None)
    if (
        type(source) is Qwen3VLImageProcessor
        and callable(getattr(source, "_resolved_size", None))
        and (source.patch_size, source.merge_size, source.temporal_patch_size) == (16, 2, 2)
        and source.do_rescale and source.do_normalize
        and source.image_mean == source.image_std == [0.5, 0.5, 0.5]
    ):
        # Use the processor's FP32 operations once for all possible unsigned pixel values.
        values = np.arange(256, dtype=np.float32) * source.rescale_factor
        table = (values - np.float32(0.5)) / np.float32(0.5)
        processor.image_processor = QwenImageInputs(source, table)


def for_review(processor: Any) -> Any:
    """A single review owns one fixed image across its optional format retries."""
    source = getattr(processor, "image_processor", None)
    if not isinstance(source, QwenImageInputs):
        return processor
    scoped = copy(processor)
    scoped.image_processor = QwenImageInputs(source.original, source.table, cache_for_review=True)
    return scoped
