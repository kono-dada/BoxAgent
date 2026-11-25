"""Reduce repeated Q and K loads during Qwen Gated DeltaNet prefill on M3 Pro."""

import sys
from importlib import import_module
from typing import Any


def _make_kernel(mx: Any, *, masked: bool) -> Any:
    mask_input = ["mask"] if masked else []
    active = "mask[token]" if masked else "true"
    return mx.fast.metal_kernel(
        name="boxagent_wuwa_gated_delta" + ("_masked" if masked else ""),
        input_names=["q", "k", "v", "g", "beta", "state_in", "length", *mask_input],
        output_names=["y", "state_out"],
        source="""
            const uint head = thread_position_in_grid.z;
            const uint row_start = thread_position_in_grid.y * 4;
            const uint lane = thread_index_in_simdgroup;
            const uint key_start = lane * 4;
            const uint state_offset = (head * 128 + row_start) * 128 + key_start;
            float state[4][4];
            for (uint row = 0; row < 4; ++row)
                for (uint key = 0; key < 4; ++key)
                    state[row][key] = state_in[state_offset + row * 128 + key];

            for (uint token = 0; token < length[0]; ++token) {
                const uint gate = token * 16 + head;
                const uint values = gate * 128 + row_start;
                float output[4] = {0.0f};
                if (ACTIVE_TOKEN) {
                    const uint offset = gate * 128 + key_start;
                    float keys[4], queries[4];
                    for (uint key = 0; key < 4; ++key) {
                        keys[key] = float(k[offset + key]);
                        queries[key] = float(q[offset + key]);
                    }
                    const float decay = g[gate];
                    const float mix = beta[gate];
                    for (uint row = 0; row < 4; ++row) {
                        float memory = 0.0f;
                        for (uint key = 0; key < 4; ++key) {
                            state[row][key] *= decay;
                            memory += state[row][key] * keys[key];
                        }
                        const float delta = (float(v[values + row]) - simd_sum(memory)) * mix;
                        for (uint key = 0; key < 4; ++key) {
                            state[row][key] = state[row][key] + keys[key] * delta;
                            output[row] += state[row][key] * queries[key];
                        }
                    }
                    for (uint row = 0; row < 4; ++row)
                        output[row] = simd_sum(output[row]);
                }
                if (lane == 0)
                    for (uint row = 0; row < 4; ++row)
                        y[values + row] = bfloat16_t(output[row]);
            }
            for (uint row = 0; row < 4; ++row)
                for (uint key = 0; key < 4; ++key)
                    state_out[state_offset + row * 128 + key] = state[row][key];
        """.replace("ACTIVE_TOKEN", active),
    )


def enable_delta_prefill(model: Any) -> None:
    import mlx.core as mx

    if (
        getattr(model.config, "model_type", None) != "qwen3_5"
        or mx.default_device() != mx.gpu
        or mx.device_info().get("architecture") != "applegpu_g15s"
    ):
        return

    backend: Any = import_module("mlx_vlm.models.qwen3_5.gated_delta")
    original = backend.gated_delta_kernel
    report_first_use = False

    def prefill(
        q: Any,
        k: Any,
        v: Any,
        g: Any,
        beta: Any,
        state: Any,
        mask: Any = None,
    ) -> tuple[Any, Any]:
        nonlocal report_first_use
        length = q.shape[1]
        if (
            length < 128
            or q.shape != (1, length, 16, 128)
            or k.shape != q.shape
            or v.shape != q.shape
            or q.dtype != mx.bfloat16
            or k.dtype != q.dtype
            or v.dtype != q.dtype
            or g.shape != (1, length, 16)
            or beta.shape != g.shape
            or g.dtype != mx.float32
            or beta.dtype != mx.bfloat16
            or state.shape != (1, 16, 128, 128)
            or state.dtype != mx.float32
            or (mask is not None and (mask.shape != (1, length) or mask.dtype != mx.bool_))
        ):
            return original(q, k, v, g, beta, state, mask)
        inputs = [q, k, v, g, beta, state, mx.array([length], dtype=mx.uint32)]
        if mask is not None:
            inputs.append(mask)
        y, after = kernels[mask is not None](
            inputs=inputs,
            grid=(32, 32, 16),
            threadgroup=(32, 4, 1),
            output_shapes=[v.shape, state.shape],
            output_dtypes=[v.dtype, state.dtype],
        )
        if report_first_use:
            print("Local vision uses the grouped Gated DeltaNet Metal kernel", file=sys.stderr)
            report_first_use = False
        return y, after

    try:
        kernels = {masked: _make_kernel(mx, masked=masked) for masked in (False, True)}
        # Force Metal compilation before serving requests and check both mask variants.
        values = mx.broadcast_to((mx.arange(128) / 2048).astype(mx.bfloat16), (1, 128, 16, 128))
        gate = mx.full((1, 128, 16), 0.875, dtype=mx.float32)
        mix = mx.full(gate.shape, 0.5, dtype=mx.bfloat16)
        state = mx.full((1, 16, 128, 128), 0.016, dtype=mx.float32)
        for mask in (None, mx.arange(128)[None, :] % 5 != 0):
            args = (values, values, values, gate, mix, state, mask)
            actual, expected = prefill(*args), original(*args)
            if not all(mx.array_equal(a, b).item() for a, b in zip(actual, expected, strict=True)):
                raise RuntimeError("Metal prefill differs from the stock recurrence")
    except (RuntimeError, ValueError) as exc:
        print(f"Metal prefill unavailable; using the stock kernel: {exc}", file=sys.stderr)
        return
    report_first_use = True
    backend.gated_delta_kernel = prefill
