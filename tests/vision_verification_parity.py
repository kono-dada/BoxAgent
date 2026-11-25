"""Run with the MLX vision interpreter on the M3 Pro to check the quantized verifier."""

import sys
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn
from mlx_vlm.models.qwen3_5.speculative_verifier import Qwen3_5ExactSpeculativeVerifier


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from vision_projection import matrix_verifier

    project = matrix_verifier()
    assert project is not None, "This parity check requires the M3 Pro Metal kernel"
    original = Qwen3_5ExactSpeculativeVerifier()
    mx.random.seed(447)
    comparisons = 0
    for width in (512, 1024, 3584):
        dense = nn.Linear(width, 128, bias=False)
        dense.weight = dense.weight.astype(mx.bfloat16)
        layer = dense.to_quantized(group_size=64, bits=4)
        ordinary = mx.random.normal((1, 8, width)).astype(mx.bfloat16)
        # Each group includes a tiny, nonzero input and cancellation; the scalar
        # branch must retain the verifier's sum order even across a wide exponent range.
        mixed = mx.where(mx.arange(width) % 16 == 0, ordinary * 2.0**-35, ordinary)
        for hidden in (ordinary, mixed, mx.zeros_like(ordinary)):
            expected = original.quantized_linear(layer, hidden)
            actual = project(layer, hidden)
            assert actual is not None and expected is not None
            assert mx.array_equal(expected, actual).item(), width
            comparisons += actual.size
        for length in (1, 2, 3, 4, 5, 6, 7, 9):
            assert project(layer, mx.zeros((1, length, width), dtype=mx.bfloat16)) is None
        assert project(layer, ordinary.astype(mx.float16)) is None
    print(f"Matched {comparisons} full projection values, including wide-exponent blocks.")


if __name__ == "__main__":
    main()
