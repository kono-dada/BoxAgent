"""Exercise the local Qwen prefill weight cache with the installed MLX runtime."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import mlx.core as mx
import numpy as np
from mlx_vlm.utils import load


def check_image_features(model: Any) -> None:
    from window_summary_worker import enable_vision_prefill

    enable_vision_prefill(model)
    original_compile = mx.compile
    encodes = 0

    def count_vision_calls(fn: Callable[[mx.array], mx.array]) -> Callable[[mx.array], mx.array]:
        compiled = original_compile(fn)

        def counted(patches: mx.array) -> mx.array:
            nonlocal encodes
            encodes += 1
            return compiled(patches)

        return counted if fn.__name__ == "encode" else compiled

    def embed(pixels: mx.array, shape: tuple[int, int], text_token: int) -> mx.array:
        cfg = model.language_model.config
        tokens = [cfg.vision_start_token_id, *([model.config.image_token_index] * 16),
                  cfg.vision_end_token_id, text_token]
        result = model.get_input_embeddings(
            mx.array([tokens]), pixels, image_grid_thw=mx.array([[1, *shape]]),
        ).inputs_embeds
        mx.eval(result)
        return result

    data = np.random.default_rng(43026).normal(size=(64, 1536)).astype(np.float32)
    changed = data.copy()
    changed.view(np.uint32)[-1, -1] ^= np.uint32(1)
    with patch.object(mx, "compile", side_effect=count_vision_calls):
        original = embed(mx.array(data), (8, 8), 25)
        other = embed(mx.array(data.copy()), (8, 8), 26)
        assert encodes == 1
        assert mx.array_equal(original[:, :-1], other[:, :-1]).item()
        assert not mx.array_equal(original[:, -1], other[:, -1]).item()
        embed(mx.array(changed), (8, 8), 25)
        assert encodes == 2  # The last bit differs outside the fast rejection samples.
        embed(mx.array(changed), (4, 16), 25)
        assert encodes == 3  # Same patches, different spatial layout.
        recovered = embed(mx.array(data), (8, 8), 25)
        assert encodes == 4 and mx.array_equal(original, recovered).item()
        for _ in range(2):
            embed(mx.array(data).astype(mx.bfloat16), (8, 8), 25)
        assert encodes == 6
    print("Matched repeated vision features; changed text, pixels, grid and dtype exercised.")


def check_recurrent_snapshot() -> None:
    import wuwa_mlx_prefill as prefill

    cut = prefill._IMAGE_PREFIX_TOKENS
    length = cut + 33
    shape = (1, length, 16, 128)
    mx.random.seed(964)
    q, k, v = [(mx.random.normal(shape) / 16).astype(mx.bfloat16) for _ in range(3)]
    g = mx.full((1, length, 16), 0.98, dtype=mx.float32)
    beta = mx.full(g.shape, 0.35, dtype=mx.bfloat16)
    state = mx.zeros((1, 16, 128, 128), dtype=mx.float32)
    from mlx_vlm.models.qwen3_5 import gated_delta

    for masked in (False, True):
        mask = (mx.arange(length)[None] % 7) != 3 if masked else None
        inputs = [q, k, v, g, beta, state, mx.array([cut], dtype=mx.uint32)]
        if mask is not None:
            inputs.append(mask)
        _, expected = prefill._make_kernel(mx, masked=masked)(
            inputs=inputs, grid=(32, 32, 16), threadgroup=(32, 4, 1),
            output_shapes=[(1, cut, 16, 128), state.shape], output_dtypes=[v.dtype, state.dtype],
        )
        actual = prefill._prefix_recurrence(gated_delta, k, v, g, beta, state, mask)
        assert actual is not None and mx.array_equal(actual, expected).item()
    print("Masked and unmasked deferred states matched the original recurrence.")


def check_image_prefix(model: Any) -> None:
    import wuwa_mlx_prefill as prefill

    prefill.enable_delta_prefill(model)
    language = model.language_model
    history = prefill.ImagePrefixCache()
    pixels = mx.array(np.random.default_rng(275).normal(size=(3840, 1536)).astype(np.float32))
    grid = mx.array([[1, 60, 64]])
    visual_tokens = [
        language.config.vision_start_token_id, *([model.config.image_token_index] * 960),
        language.config.vision_end_token_id,
    ]
    starts: list[int] = []

    def request(length: int, word: int) -> tuple[mx.array, Any]:
        ids = mx.array([visual_tokens + [word] * (length + 1 - len(visual_tokens))])
        return ids, model.get_input_embeddings(ids, pixels, image_grid_thw=grid)

    def evaluate(ids: mx.array, features: Any, *, reuse: bool) -> list[Any]:
        cache = language.make_cache()

        def run(start: int) -> None:
            starts.append(start)
            language(ids[:, start:-1], inputs_embeds=features.inputs_embeds[:, start:-1],
                     position_ids=features.position_ids, rope_deltas=features.rope_deltas,
                     cache=cache, skip_logits=True)
        if reuse:
            history.prefill(features, ids, cache, 1536, language, run)
        else:
            run(0)
        mx.eval([entry.state for entry in cache])
        return cache

    with patch.object(prefill, "_normal_memory_pressure", return_value=True):
        lengths = (1055, 1064, 1035, 1052, 1088, 1146, 1279, 1303, 1408, 1536, 1025)
        for i, length in enumerate(lengths):
            ids, features = request(length, 40 + i)
            expected = evaluate(ids, features, reuse=False)
            actual = evaluate(ids, features, reuse=True)
            assert starts[-1] == (0 if i < 2 else prefill._IMAGE_PREFIX_TOKENS)
            matches = [mx.array_equal(a, b) for entry, other in zip(expected, actual, strict=True)
                       for a, b in zip(entry.state, other.state, strict=True)]
            assert mx.all(mx.stack(matches)).item(), (length, [v.item() for v in matches])
        saved = history.saved
        assert saved is not None

        moving = prefill.ImagePrefixCache()
        fresh_starts: list[int] = []

        def fresh(start: int) -> None:
            assert prefill._capture.get() is None and not prefill._suffix.get()
            fresh_starts.append(start)

        for _ in range(3):
            current = SimpleNamespace(prefix_image=object())
            moving.prefill(current, ids, language.make_cache(), 1536, language, fresh)
            assert moving.saved is None
        assert fresh_starts == [0, 0, 0]

        def misses(marker: Any, tokens: mx.array, chunk: int) -> None:
            history.saved = saved
            history.previous_features = features.prefix_image
            called: list[int] = []
            history.prefill(marker, tokens, language.make_cache(), chunk, language, called.append)
            assert called == [0]

        misses(features, ids, 512)
        misses(features, ids[:, :1025], 1536)
        misses(features, mx.concatenate([ids[:, :1] + 1, ids[:, 1:]], axis=1), 1536)
        misses(SimpleNamespace(prefix_image=object(), image_end=features.image_end,
                               position_ids=features.position_ids), ids, 1536)
        for axis in range(3):
            changed = np.array(features.position_ids, copy=True)
            changed[axis, 0, prefill._IMAGE_PREFIX_TOKENS - 1] += 1
            misses(SimpleNamespace(prefix_image=features.prefix_image, image_end=features.image_end,
                                   position_ids=mx.array(changed)), ids, 1536)
        history.saved = saved
        history.previous_features = features.prefix_image
        called: list[int] = []

        def simulate_eviction(start: int) -> None:
            called.append(start)
            if start:
                raise prefill._PrefixUnavailable

        history.prefill(features, ids, language.make_cache(), 1536, language, simulate_eviction)
        assert called == [prefill._IMAGE_PREFIX_TOKENS, 0] and history.saved is None
    history.saved = saved
    with patch.object(prefill, "_normal_memory_pressure", return_value=False):
        called = []
        history.prefill(features, ids, language.make_cache(), 1536, language, called.append)
        assert called == [0] and history.saved is None
    print("All 48 layer-state arrays matched on full and resumed prompts. "
          "Changed images, tokens, positions, chunking and memory pressure exercised.")


def main(model_path: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import wuwa_mlx_prefill as prefill

    model, _ = load(str(model_path))
    check_image_features(model)
    prefill.enable_language_prefill(model)
    layer = model.language_model.model.layers[0]
    mx.random.seed(43026)
    x = mx.random.normal((1, 1055, 1024)).astype(mx.bfloat16)
    mx.eval(x)
    # Compare with the compact-weight path independently of which projections are retained.
    def evaluate() -> list[mx.array]:
        result = [layer.mlp(x), layer.linear_attn.in_proj_qkv(x), layer.linear_attn.in_proj_z(x)]
        mx.eval(*result)
        return result

    with patch.object(prefill, "_normal_memory_pressure") as normal, \
            patch.object(prefill, "monotonic") as clock:
        clock.return_value = 10.0
        normal.return_value = False
        expected = evaluate()
        warm = mx.get_active_memory()
        clock.return_value = 12.0
        normal.return_value = True
        one = evaluate()
        two = evaluate()
        assert all(mx.array_equal(a, b).item() for a, b in zip(expected, one, strict=True))
        assert all(mx.array_equal(a, b).item() for a, b in zip(expected, two, strict=True))
        del one, two
        retained = mx.get_active_memory()
        assert retained > warm
        clock.return_value = 14.0
        normal.return_value = False
        after = evaluate()
        assert all(mx.array_equal(a, b).item() for a, b in zip(expected, after, strict=True))
        del after
        assert mx.get_active_memory() < retained
        clock.return_value = 16.0
        normal.return_value = True
        again = evaluate()
        assert all(mx.array_equal(a, b).item() for a, b in zip(expected, again, strict=True))
    print("Matched cold, retained, released and rebuilt prefill outputs.")
    check_recurrent_snapshot()
    check_image_prefix(model)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("model_path", type=Path)
    main(parser.parse_args().model_path)
