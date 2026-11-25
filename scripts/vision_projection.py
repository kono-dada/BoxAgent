"""Full eight-token Qwen verification blocks on the M3 Pro's matrix units."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Any

import mlx.core as mx


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
