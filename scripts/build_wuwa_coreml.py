"""Build masked, full-attention Core ML image encoders for local Qwen."""

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from wuwa_coreml import FEATURE_WIDTH, PATCH_WIDTH, source_fingerprints

VISION_CHANNELS = 768
ATTENTION_HEADS = 12
HEAD_WIDTH = VISION_CHANNELS // ATTENTION_HEADS
VISION_BLOCKS = 12


def make_encoder(model: Any, patches: int) -> Any:
    import coremltools as ct
    import mlx.core as mx
    import numpy as np
    from coremltools.converters.mil.mil import Builder, types

    mb: Any = Builder  # Core ML registers its operations on Builder at import time.
    tower = model.vision_tower
    projection = tower.patch_embed.proj
    if (
        projection.weight.shape != (VISION_CHANNELS, 2, 16, 16, 3)
        or tower.spatial_merge_size != 2
        or len(tower.blocks) != VISION_BLOCKS
        or tower.deepstack_visual_indexes
        or tower.merger.use_postshuffle_norm
    ):
        raise ValueError("The model does not have the supported Qwen3.5-0.8B vision tower")

    def half(value: Any) -> Any:
        return np.asarray(value.astype(mx.float16))

    patch = projection.weight.moveaxis(-1, 1).reshape(VISION_CHANNELS, PATCH_WIDTH)

    def pointwise(x: Any, weight: Any, bias: Any) -> Any:
        return mb.conv(x=x, weight=half(weight)[:, :, None, None].copy(), bias=half(bias))

    def linear(x: Any, layer: Any) -> Any:
        return pointwise(x, layer.weight, layer.bias)

    def norm(x: Any, layer: Any) -> Any:
        return mb.layer_norm(
            x=x,
            axes=[1],
            gamma=half(layer.weight),
            beta=half(layer.bias),
            epsilon=np.float16(layer.eps),
        )

    def rotate(x: Any, cosine: Any, sine: Any) -> Any:
        heads = mb.reshape(x=x, shape=(1, ATTENTION_HEADS, HEAD_WIDTH, patches))
        first, second = mb.split(x=heads, num_splits=2, axis=2)
        swapped = mb.concat(values=(mb.mul(x=second, y=np.float16(-1)), first), axis=2)
        turned = mb.add(x=mb.mul(x=heads, y=cosine), y=mb.mul(x=swapped, y=sine))
        return mb.reshape(x=turned, shape=(1, VISION_CHANNELS, 1, patches))

    def attend_heads(q: Any, k: Any, v: Any, key_bias: Any) -> Any:
        per_head = (mb.split(x=tensor, num_splits=ATTENTION_HEADS, axis=1) for tensor in (q, k, v))
        features = []
        for query, key, value in zip(*per_head, strict=True):
            key_t = mb.transpose(x=key, perm=[0, 3, 2, 1])
            scores = mb.einsum(values=(key_t, query), equation="bkhc,bchq->bkhq")
            scores = mb.add(x=scores, y=key_bias)
            weights = mb.softmax(x=scores, axis=1)
            features.append(mb.einsum(values=(value, weights), equation="bchk,bkhq->bchq"))
        return mb.concat(values=features, axis=1)

    rotary_shape = (1, 1, HEAD_WIDTH, patches)

    @mb.program(
        input_specs=[
            mb.TensorSpec(shape=(1, PATCH_WIDTH, 1, patches), dtype=types.fp16),
            mb.TensorSpec(shape=(1, VISION_CHANNELS, 1, patches), dtype=types.fp16),
            mb.TensorSpec(shape=rotary_shape, dtype=types.fp16),
            mb.TensorSpec(shape=rotary_shape, dtype=types.fp16),
            mb.TensorSpec(shape=(1, patches, 1, 1), dtype=types.fp16),
        ],
        opset_version=ct.target.macOS14,
    )
    def encode(pixels: Any, position: Any, cosine: Any, sine: Any, key_bias: Any) -> Any:
        x = mb.add(x=pointwise(pixels, patch, projection.bias), y=position)
        for block in tower.blocks:
            attention = block.attn
            q, k, v = mb.split(x=linear(norm(x, block.norm1), attention.qkv), num_splits=3, axis=1)
            q = mb.mul(x=rotate(q, cosine, sine), y=np.float16(HEAD_WIDTH**-0.5))
            k = rotate(k, cosine, sine)
            joined = attend_heads(q, k, v, key_bias)
            x = mb.add(x=x, y=linear(joined, attention.proj))
            mlp = block.mlp
            activated = mb.gelu(
                x=linear(norm(x, block.norm2), mlp.linear_fc1), mode="TANH_APPROXIMATION"
            )
            x = mb.add(x=x, y=linear(activated, mlp.linear_fc2))
        merger = tower.merger
        count = patches // 4
        x = mb.reshape(x=norm(x, merger.norm), shape=(VISION_CHANNELS, count, 4))
        x = mb.reshape(
            x=mb.transpose(x=x, perm=(2, 0, 1)), shape=(1, 4 * VISION_CHANNELS, 1, count)
        )
        x = linear(mb.gelu(x=linear(x, merger.linear_fc1), mode="EXACT"), merger.linear_fc2)
        features = mb.reshape(x=x, shape=(FEATURE_WIDTH, count))
        return mb.transpose(x=features, perm=(1, 0), name="out")

    return ct.convert(
        encode,
        convert_to="mlprogram",
        minimum_deployment_target=ct.target.macOS14,
        compute_precision=ct.precision.FLOAT16,
        skip_model_load=True,
    )


def build_masked(model_path: Path, output: Path, sizes: list[int]) -> None:
    import coremltools as ct
    from mlx_vlm.utils import load

    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Bundle already exists: {output}")
    if any(size <= 0 or size % 4 for size in sizes):
        raise ValueError("Each patch capacity must be positive and divisible by the spatial merge")
    source = source_fingerprints(model_path)
    model, _ = load(str(model_path))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vision-masked-", dir=output.parent) as work:
        staging = Path(work) / "bundle"
        staging.mkdir()
        for size in sorted(set(sizes)):
            directory = staging / str(size)
            directory.mkdir()
            program = make_encoder(model, size)
            package = Path(work) / f"vision-{size}.mlpackage"
            program.save(str(package))
            ct.models.utils.compile_model(
                str(package), destination_path=str(directory / "vision.mlmodelc"),
            )
        manifest = {"version": 1, "source_sha256": source, "capacities": sorted(set(sizes))}
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        staging.rename(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--patch-buckets", nargs="+", type=int, default=[1024, 2048, 4096],
                        help="Build masked full-attention models for these patch capacities")
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    build_masked(args.model.resolve(), args.output.absolute(), args.patch_buckets)


if __name__ == "__main__":
    main()
