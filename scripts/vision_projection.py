"""Full eight-token Qwen verification blocks on the M3 Pro's matrix units."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import mlx.core as mx
import numpy as np
from numpy.typing import NDArray


@lru_cache(maxsize=1)
def matrix_verifier() -> Callable[[Any, mx.array], mx.array | None] | None:
    if mx.default_device() != mx.gpu or mx.device_info().get("architecture") != "applegpu_g15s":
        return None
    prepare = mx.fast.metal_kernel(
        name="boxagent_verify_input_groups", input_names=["x"], output_names=["sums", "sensitive"],
        source="""
            const uint group = thread_position_in_grid.x;
            const uint start = group * 16;
            float sum = 0.0f;
            for (uint i = 0; i < 16; i += 4) {
                uint k = start + i;
                // Match the verifier's four-input BF16 addition before accumulating in FP32.
                sum += x[k] + x[k + 1] + x[k + 2] + x[k + 3];
            }
            sums[group] = sum;
            int lowest_bit = 128;
            float magnitude = 0.0f;
            bool special = false;
            for (uint i = 0; i < 16; ++i) {
                float value = fabs(float(x[start + i]));
                uint bits = as_type<uint>(value);
                if (bits == 0) continue;
                uint exponent = bits >> 23;
                special |= exponent == 0 || exponent == 255;
                int lowest = int(exponent) - 127 - 23 + int(ctz((bits & 0x7fffff) | 0x800000));
                lowest_bit = min(lowest_bit, lowest);
                magnitude += value;
            }
            // All products are integer multiples of 2^lowest_bit. Below 2^24 units,
            // every partial sum is exactly representable in FP32 in any addition order.
            sensitive[group] = special || (lowest_bit < 128 &&
                !(magnitude * 15.001f < ldexp(1.0f, lowest_bit + 24)));
        """,
    )
    multiply = mx.fast.metal_kernel(
        name="boxagent_verify_affine_matrix",
        input_names=["x", "w", "scales", "biases", "sums", "sensitive"], output_names=["out"],
        header="#include <metal_simdgroup_matrix>\nusing namespace metal;",
        source="""
            constexpr uint tiles = 2, parts = 4;
            const uint base = threadgroup_position_in_grid.x * 8 * tiles;
            const uint part = simdgroup_index_in_threadgroup;
            const uint lane = thread_index_in_simdgroup;
            // A G15S lane holds two adjacent columns of an 8-by-8 matrix fragment.
            const uint row = ((lane / 4) & 4) + (lane / 2) % 4;
            const uint col = ((lane / 4) & 2) * 2 + (lane % 2) * 2;
            threadgroup float partial[tiles * 64 * 32];
            for (uint k_lane = part; k_lane < 32; k_lane += parts) {
                float result[tiles][2] = {};
                for (uint segment = 0; segment < K / 512; ++segment) {
                    uint k = k_lane * 16 + segment * 512;
                    simdgroup_float8x8 dots[tiles];
                    for (uint m = 0; m < tiles; ++m)
                        dots[m] = make_filled_simdgroup_matrix<float, 8>(0.0f);
                    for (uint step = 0; step < 2; ++step) {
                        uint a = k + step * 8;
                        simdgroup_float8x8 rhs;
                        rhs.thread_elements()[0] = float(x[col * K + a + row]);
                        rhs.thread_elements()[1] = float(x[(col + 1) * K + a + row]);
                        for (uint m = 0; m < tiles; ++m) {
                            uint n = base + 8 * m + row;
                            uint word = w[n * (K / 8) + a / 8] >> (4 * col);
                            simdgroup_float8x8 lhs;
                            lhs.thread_elements()[0] = float(word & 15);
                            lhs.thread_elements()[1] = float((word >> 4) & 15);
                            simdgroup_multiply_accumulate(dots[m], lhs, rhs, dots[m]);
                        }
                    }
                    for (uint m = 0; m < tiles; ++m) {
                        uint n = base + 8 * m + row;
                        uint group = n * (K / 64) + k / 64;
                        float scale = float(scales[group]), bias = float(biases[group]);
                        for (uint j = 0; j < 2; ++j) {
                            uint token = col + j;
                            uint g = token * (K / 16) + k / 16;
                            float dot = dots[m].thread_elements()[j];
                            if (sensitive[g]) {
                                // Keep the original four-term ordering where FP32 can round.
                                dot = 0.0f;
                                for (uint a = 0; a < 16; a += 4) {
                                    uint word = w[n * (K / 8) + (k + a) / 8] >> (4 * (a % 8));
                                    uint i = token * K + k + a;
                                    dot += float(x[i]) * (word & 15)
                                        + float(x[i + 1]) * ((word >> 4) & 15)
                                        + float(x[i + 2]) * ((word >> 8) & 15)
                                        + float(x[i + 3]) * ((word >> 12) & 15);
                                }
                            }
                            result[m][j] += scale * dot + sums[g] * bias;
                        }
                    }
                }
                for (uint m = 0; m < tiles; ++m) {
                    uint i = ((8 * m + row) * 8 + col) * 32 + k_lane;
                    partial[i] = result[m][0];
                    partial[i + 32] = result[m][1];
                }
            }
            threadgroup_barrier(mem_flags::mem_threadgroup);
            for (uint i = part; i < tiles * 64; i += parts) {
                // Reduce the same 32 lanes as the reference, in the hardware's native order.
                float value = simd_sum(partial[i * 32 + lane]);
                if (lane == 0) out[(i % 8) * N + base + i / 8] = T(value);
            }
        """,
    )

    def project(layer: Any, hidden: mx.array) -> mx.array | None:
        if (
            hidden.ndim != 3 or hidden.shape[0] != 1 or hidden.shape[1] != 8
            or hidden.dtype != mx.bfloat16 or getattr(layer, "bits", None) != 4
            or layer.group_size != 64 or layer.mode != "affine" or "bias" in layer
            or layer.biases is None or layer.weight.dtype != mx.uint32
        ):
            return None
        _, length, width = hidden.shape
        rows, packed = layer.weight.shape
        if (
            width % 512 or rows % 16 or packed * 8 != width
            or layer.scales.shape != (rows, width // 64) or layer.biases.shape != layer.scales.shape
            or layer.scales.dtype != hidden.dtype or layer.biases.dtype != hidden.dtype
        ):
            return None
        x = mx.contiguous(hidden)
        groups = length * (width // 16)
        sums, sensitive = prepare(
            inputs=[x], template=[("T", x.dtype)], grid=(groups, 1, 1),
            threadgroup=(min(256, groups), 1, 1), output_shapes=[(groups,), (groups,)],
            output_dtypes=[mx.float32, mx.uint8],
        )
        return multiply(
            inputs=[x, layer.weight, layer.scales, layer.biases, sums, sensitive],
            template=[("T", x.dtype), ("N", rows), ("K", width)],
            grid=(rows // 16 * 128, 1, 1), threadgroup=(128, 1, 1),
            output_shapes=[(1, length, rows)], output_dtypes=[x.dtype],
        )[0]

    return project


@dataclass(frozen=True)
class DraftCandidates:
    ids: NDArray[np.int32]
    activation: NDArray[np.float32]
    cutoff: NDArray[np.float32]

    @classmethod
    def allocate(cls, width: int) -> DraftCandidates:
        return cls(np.empty((width, 128), np.int32), np.empty((width, 1040), np.float32),
                   np.empty(width, np.float32))


class CandidateProjection:
    """Verify a CPU shortlist against the current target hidden states on the M3 Pro."""

    def __init__(self, head: Any, project: Callable[[Any, mx.array], mx.array | None]) -> None:
        from mlx.nn import QuantizedLinear

        self.source = head
        self.project = project
        self.head = QuantizedLinear(1024, 8, bias=False, group_size=64, bits=4, mode="affine")

    @staticmethod
    def _upper_bound(
        hidden: NDArray[np.float32], activation: NDArray[np.float32], cutoff: NDArray[np.float32],
    ) -> NDArray[np.float32]:

        # The Metal verifier adds four BF16 activations before applying an affine bias.
        # The CPU uses the actual Q8_1 values and rounded per-block sums from llama.cpp.
        def bf16(value: NDArray[np.float32]) -> NDArray[np.float32]:
            bits = value.view(np.uint32)
            rounded = bits + np.uint32(0x7FFF) + ((bits >> 16) & 1)
            return (rounded & np.uint32(0xFFFF0000)).view(np.float32)

        groups = hidden.reshape(8, 16, 16, 4)
        sums = bf16(bf16(bf16(groups[..., 0] + groups[..., 1]) + groups[..., 2]) + groups[..., 3])
        bias_error = (sums - groups.sum(axis=-1)).sum(axis=-1) - activation[:, 1024:]
        cpu = activation[:, :1024]
        # Upper bounds for *every* row in the pinned embedding: ||W||₂ < 1.061,
        # ||bias||₂ < .405. Cauchy–Schwarz bounds the change to any omitted score.
        norm = np.linalg.norm
        error = 1.061 * norm(hidden - cpu, axis=1) + 0.405 * norm(bias_error, axis=1)
        # Each affine arm has norm <= sqrt(1024) * (15*max|scale| + max|bias|) < 18.
        # 1024 FP32 additions introduce < 0.0012 times the input norm per arm.
        error += 0.0025 * (norm(hidden, axis=1) + norm(cpu, axis=1) + 1)
        upper = cutoff + error
        return upper + 0.008 * np.abs(upper) + 0.01  # includes the final BF16 rounding

    def choose(
        self, hidden: mx.array, candidates: DraftCandidates,
        full_greedy: Callable[[mx.array], mx.array], penalty: float | None = None,
        recent: list[list[int]] | None = None,
    ) -> list[int] | None:
        if hidden.shape != (1, 8, 1024) or (penalty and (penalty < 1 or recent is None)):
            return None
        ids = candidates.ids
        indices = mx.array(ids.ravel(), dtype=mx.uint32)
        for key in ("weight", "scales", "biases"):
            self.head[key] = mx.take(self.source[key], indices, axis=0)
        projected = self.project(self.head, hidden)
        if projected is None:
            return None
        row = mx.arange(8)
        scores = projected.reshape(8, 8, ids.shape[1])[row, row]
        cutoff = candidates.cutoff
        if penalty and recent is not None:
            repeated = mx.array(np.stack([np.isin(tokens, seen) for tokens, seen in
                                          zip(ids, recent, strict=True)]))
            scores = mx.where(repeated, mx.where(scores < 0, scores * penalty, scores / penalty),
                              scores)
            # Omitted CPU scores may already include the draft's repetition penalty.
            # Undo its largest possible decrease before bounding the target's raw scores.
            cutoff = np.where(cutoff >= 0, cutoff * penalty, cutoff / penalty)
        scores = scores.astype(mx.float32)
        target = hidden.astype(mx.float32)
        mx.eval(scores, target)
        values = np.asarray(scores)
        top = values.max(axis=1)
        tied = values == top[:, None]
        next_score = np.where(tied, -np.inf, values).max(axis=1)
        upper = self._upper_bound(np.asarray(target)[0], candidates.activation, cutoff)
        # With these magnitudes the largest normalized log probability lies in
        # [-13,1]. BF16 spacing there is <= 1/16. Exact ties choose the lowest ID.
        clear = (np.abs(top) <= 64) & (top - np.maximum(next_score, upper) > 0.067)
        unknown = np.flatnonzero(~clear)
        if len(unknown) > 3 or (penalty and len(unknown)):
            return None
        winners = np.where(tied, ids, np.iinfo(np.int32).max).min(axis=1)
        if len(unknown):
            missed = mx.take(hidden, mx.array(unknown), axis=1)
            winners[unknown] = np.asarray(full_greedy(missed)).ravel()
        return [int(token) for token in winners]
