"""Run with the MLX vision interpreter on the M3 Pro to check the quantized verifier."""

import sys
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn
import numpy as np
from mlx_vlm.models.qwen3_5.speculative_verifier import Qwen3_5ExactSpeculativeVerifier


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from vision_projection import CandidateProjection, DraftCandidates, matrix_verifier
    from wuwa_cpu_draft import greedy_tokens

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
            for rows in ([5], [7, 1], [2, 4, 0]):
                # The selective fallback projects these rows with the usual short-block kernel.
                indices = mx.array(rows)
                single = original.quantized_linear(layer, mx.take(hidden, indices, axis=1))
                assert single is not None
                assert mx.array_equal(single, mx.take(expected, indices, axis=1)).item()
        for length in (1, 2, 3, 4, 5, 6, 7, 9):
            assert project(layer, mx.zeros((1, length, width), dtype=mx.bfloat16)) is None
        assert project(layer, ordinary.astype(mx.float16)) is None
    # Tied shortlist maxima must select the lowest vocabulary ID. A stronger
    # unlisted token must be recovered by the original full-vocabulary projection.
    linear = nn.Linear(1024, 256, bias=False)
    weight = np.zeros((256, 1024), np.float32)
    weight[[5, 9], 0] = 0.25
    weight[231, 1] = 0.5
    linear.weight = mx.array(weight, dtype=mx.bfloat16)
    layer = linear.to_quantized(group_size=64, bits=4)
    source = np.zeros((8, 1024), np.float32)
    source[:, 0], source[2, 1] = 16, 32
    hidden = mx.array(source, dtype=mx.bfloat16)[None]
    candidates = DraftCandidates.allocate(8)
    candidates.ids[:] = np.arange(127, -1, -1, dtype=np.int32)
    candidates.activation[:] = 0
    candidates.activation[:, :1024] = source
    full = project(layer, hidden)
    assert full is not None
    candidates.cutoff[:] = np.asarray(full[0, :, 128:].max(axis=-1).astype(mx.float32))
    projection = CandidateProjection(layer, project)

    def full_greedy(x: mx.array) -> mx.array:
        out = original.quantized_linear(layer, x)
        assert out is not None
        return greedy_tokens(out)

    chosen = projection.choose(hidden, candidates, full_greedy)
    expected_ids = greedy_tokens(full).flatten().tolist()
    assert expected_ids[0] == 5 and expected_ids[2] == 231
    assert chosen == expected_ids
    candidates.cutoff[:] = 100
    assert projection.choose(hidden, candidates, full_greedy) is None
    candidates.cutoff[:] = -0.01
    seen = [[5] for _ in range(8)]
    candidates.cutoff[2] = 100
    assert projection.choose(hidden, candidates, full_greedy, 1.15, seen) is None
    source[2, 1] = 0
    hidden = mx.array(source, dtype=mx.bfloat16)[None]
    candidates.activation[:, :1024] = source
    candidates.cutoff[:] = 0
    assert projection.choose(hidden, candidates, full_greedy, 1.15, seen) == [9] * 8
    print(f"Matched {comparisons} full projection values, including wide-exponent blocks.")


if __name__ == "__main__":
    main()
