"""Exercise the local Qwen prefill weight cache with the installed MLX runtime."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import mlx.core as mx
from mlx_vlm.utils import load


def main(model_path: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import wuwa_mlx_prefill as prefill

    model, _ = load(str(model_path))
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("model_path", type=Path)
    main(parser.parse_args().model_path)
