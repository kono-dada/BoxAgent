"""Build a local Qwen vision acceleration bundle with native text prefill."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import coremltools as ct
import mlx.core as mx
import numpy as np
from coremltools.converters.mil.mil import Builder, types
from mlx_vlm.models.qwen3_5.qwen3_5 import Model
from mlx_vlm.utils import load
from wuwa_coreml import source_fingerprints

SEQ = 1088
HIDDEN = 1024
CONTEXT = 2048
mb: Any = Builder  # Core ML registers its operations on Builder at import time.


def dense_weight(layer: Any) -> mx.array:
    if not hasattr(layer, "bits"):
        return layer.weight
    return mx.dequantize(
        layer.weight,
        layer.scales,
        layer.get("biases"),
        group_size=layer.group_size,
        bits=layer.bits,
        mode=layer.mode,
    )


def half(x: mx.array) -> np.ndarray:
    return np.asarray(x.astype(mx.float16)).copy()


def conv(x: Any, layers: tuple[Any, ...]) -> Any:
    weights = np.concatenate([half(dense_weight(proj)) for proj in layers], axis=0)
    biases = [getattr(proj, "bias", None) for proj in layers]
    if any(bias is not None for bias in biases):
        bias = np.concatenate(
            [
                half(b) if b is not None else np.zeros(proj.weight.shape[0], np.float16)
                for proj, b in zip(layers, biases, strict=True)
            ]
        )
        return mb.conv(x=x, weight=weights[:, :, None, None], bias=bias)
    return mb.conv(x=x, weight=weights[:, :, None, None])


def norm(x: Any, layer: Any) -> Any:
    squares = mb.reduce_mean(x=mb.square(x=x), axes=[1], keep_dims=True)
    inverse = mb.rsqrt(x=mb.add(x=squares, y=np.float16(layer.eps)))
    normed = mb.mul(x=x, y=inverse)
    return mb.mul(x=normed, y=half(layer.weight).reshape(1, -1, 1, 1))


def as_tokens(x: Any, fields: int, name: str) -> Any:
    return mb.transpose(x=mb.reshape(x=x, shape=(fields, SEQ)), perm=(1, 0), name=name)


def finish_layer(hidden: Any, context: Any, layer: Any) -> Any:
    out = layer.linear_attn.out_proj if layer.is_linear else layer.self_attn.o_proj
    h = mb.add(x=hidden, y=conv(context, (out,)))
    x = norm(h, layer.post_attention_layernorm)
    ffn = layer.mlp
    gate, up = mb.split(x=conv(x, (ffn.gate_proj, ffn.up_proj)), num_splits=2, axis=1)
    activate = mb.mul(x=gate, y=mb.sigmoid(x=gate))
    return mb.add(x=h, y=conv(mb.mul(x=activate, y=up), (ffn.down_proj,)))


def full_neural_attention(x: Any, attention: Any, cosine: Any, sine: Any) -> tuple[Any, Any, Any]:
    def rotary(v: Any) -> Any:
        active, tail = mb.split(x=v, split_sizes=[64, 192], axis=1)
        first, second = mb.split(x=active, num_splits=2, axis=1)
        rotate = mb.concat(values=(mb.mul(x=second, y=np.float16(-1)), first), axis=1)
        changed = mb.add(x=mb.mul(x=active, y=cosine), y=mb.mul(x=rotate, y=sine))
        return mb.concat(values=(changed, tail), axis=1)

    projection = conv(x, (attention.q_proj, attention.k_proj, attention.v_proj))
    projected_q, projected_k, projected_v = mb.split(
        x=projection, split_sizes=[4096, 512, 512], axis=1
    )
    value = mb.split(x=projected_v, num_splits=2, axis=1)
    key = [rotary(norm(k, attention.k_norm)) for k in mb.split(x=projected_k, num_splits=2, axis=1)]
    key_t = [mb.transpose(x=k, perm=[0, 3, 2, 1]) for k in key]
    mask = np.where(np.tri(SEQ, dtype=np.bool_).T, np.float16(0), np.float16(-10000))
    mask = mask[None, :, None, :].copy()
    answer = []
    for i, chunk in enumerate(mb.split(x=projected_q, num_splits=8, axis=1)):
        q, gate = mb.split(x=chunk, num_splits=2, axis=1)
        q = mb.mul(x=rotary(norm(q, attention.q_norm)), y=np.float16(0.0625))
        scores = mb.einsum(values=(key_t[i // 4], q), equation="bkhc,bchq->bkhq")
        probs = mb.softmax(x=mb.add(x=scores, y=mask), axis=1)
        out = mb.einsum(values=(value[i // 4], probs), equation="bchk,bkhq->bchq")
        answer.append(mb.mul(x=out, y=mb.sigmoid(x=gate)))
    return (
        mb.concat(values=answer, axis=1),
        mb.concat(values=key, axis=1, name="key"),
        mb.identity(x=projected_v, name="value"),
    )


def build_stage(layers: list[Any], index: int, output: Path) -> None:
    with_full_attention = not layers[index].is_linear

    def next_inputs(hidden: Any, following: int) -> tuple[Any, ...]:
        hidden = mb.identity(x=hidden, name="hidden_out")
        if following == len(layers):
            return (hidden,)
        layer = layers[following]
        recurrent = layer.linear_attn
        x = norm(hidden, layer.input_layernorm)
        mixed = conv(x, (recurrent.in_proj_qkv,))
        extra = conv(x, (recurrent.in_proj_z, recurrent.in_proj_b, recurrent.in_proj_a))
        return hidden, as_tokens(mixed, 6144, "mixed"), as_tokens(extra, 2080, "extra")

    hidden_spec = mb.TensorSpec(shape=(1, HIDDEN, 1, SEQ), dtype=types.fp16)
    context_spec = mb.TensorSpec(shape=(1, CONTEXT, 1, SEQ), dtype=types.fp16)
    if with_full_attention:
        rotary_specs = [mb.TensorSpec(shape=(1, 64, 1, SEQ), dtype=types.fp16) for _ in range(2)]

        @mb.program(
            input_specs=[hidden_spec, context_spec, *rotary_specs], opset_version=ct.target.macOS14
        )
        def full_attention(hidden: Any, context: Any, cosine: Any, sine: Any) -> Any:
            h = finish_layer(hidden, context, layers[index - 1])
            layer = layers[index]
            attended, k, v = full_neural_attention(
                norm(h, layer.input_layernorm), layer.self_attn, cosine, sine
            )
            h = finish_layer(h, attended, layer)
            return (*next_inputs(h, index + 1), k, v)

        program = full_attention
    elif index == 0:

        @mb.program(input_specs=[hidden_spec], opset_version=ct.target.macOS14)
        def first_stage(hidden: Any) -> Any:
            return next_inputs(hidden, 0)

        program = first_stage
    else:

        @mb.program(input_specs=[hidden_spec, context_spec], opset_version=ct.target.macOS14)
        def recurrent_stage(hidden: Any, context: Any) -> Any:
            return next_inputs(finish_layer(hidden, context, layers[index - 1]), index)

        program = recurrent_stage

    names = ["hidden_out"]
    if index + int(with_full_attention) < len(layers):
        names.extend(("mixed", "extra"))
    if with_full_attention:
        names.extend(("key", "value"))
    model = ct.convert(
        program,
        convert_to="mlprogram",
        minimum_deployment_target=ct.target.macOS14,
        compute_precision=ct.precision.FLOAT16,
        skip_model_load=True,
        outputs=[ct.TensorType(name=name, dtype=np.float16) for name in names],
    )
    if not isinstance(model, ct.models.MLModel):
        raise RuntimeError("Core ML conversion did not produce an MLModel")
    with tempfile.TemporaryDirectory(dir=output.parent) as work:
        source = Path(work) / "stage.mlpackage"
        model.save(str(source))
        ct.models.utils.compile_model(str(source), destination_path=str(output))


def build(
    model_path: Path, output: Path, *, vision_masked: Path | None = None,
    mtp_drafter: Path | None = None, cpu_draft: Path | None = None,
) -> None:
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Bundle already exists: {output}")
    source = source_fingerprints(model_path)
    if vision_masked is not None:
        manifest = json.loads((vision_masked / "manifest.json").read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or manifest.get("version") != 1
                or manifest.get("source_sha256") != source):
            raise ValueError("The masked vision bundle was built for different model weights")
    draft_files = ("config.json", "model.safetensors")
    if mtp_drafter is not None and not all((mtp_drafter / name).is_file() for name in draft_files):
        raise ValueError("An MTP drafter needs config.json and model.safetensors")
    if cpu_draft is not None:
        cpu_manifest = json.loads((cpu_draft / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(cpu_manifest, dict) or cpu_manifest.get("source_sha256") != source:
            raise ValueError("The CPU draft bundle was built for different model weights")
    model, _ = load(str(model_path))
    if not isinstance(model, Model):
        raise ValueError("Expected a Qwen3.5 model")
    language = model.language_model
    layers = language.model.layers
    if (
        len(layers) != 24
        or language.args.hidden_size != HIDDEN
        or any(layer.is_linear != (i % 4 != 3) for i, layer in enumerate(layers))
        or layers[3].self_attn.rotary_emb.dim != 64
    ):
        raise ValueError("The model does not have the supported Qwen3.5-0.8B language tower")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wuwa-prefill-", dir=output.parent) as work:
        bundle = Path(work) / "bundle"
        bundle.mkdir()
        if vision_masked is not None:
            shutil.copytree(vision_masked, bundle / "vision-masked")
        text = bundle / "text"
        text.mkdir()
        if mtp_drafter is not None:
            draft_output = text / "mtp"
            draft_output.mkdir()
            for name in draft_files:
                shutil.copy2(mtp_drafter / name, draft_output / name)
        if cpu_draft is not None:
            shutil.copytree(cpu_draft, text / "cpu-draft")
        for i in range(len(layers)):
            if i and not layers[i - 1].is_linear:
                continue
            build_stage(layers, i, text / f"part-{i:02}.mlmodelc")
        scripts = Path(__file__).resolve().parent
        subprocess.run(
            [
                "xcrun",
                "clang",
                "-dynamiclib",
                "-fobjc-arc",
                "-fblocks",
                "-O3",
                "-mcpu=apple-m1",
                "-mmacosx-version-min=14.0",
                "-DACCELERATE_NEW_LAPACK",
                str(scripts / "wuwa_text_attention.c"),
                str(scripts / "wuwa_text_bridge.m"),
                "-framework",
                "Accelerate",
                "-framework",
                "CoreML",
                "-framework",
                "Foundation",
                "-o",
                str(text / "libwuwa_text.dylib"),
            ],
            check=True,
        )
        text_manifest = {"version": 1, "source_sha256": source, "sequence_length": SEQ}
        (text / "manifest.json").write_text(
            json.dumps(text_manifest, indent=2) + "\n", encoding="utf-8"
        )
        bundle.rename(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--vision-masked", type=Path, help="Compiled masked image encoders")
    parser.add_argument("--mtp-drafter", type=Path, help="Local mlx-vlm Qwen3.5 MTP head")
    parser.add_argument("--cpu-draft", type=Path, help="Local CPU draft runtime bundle")
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    build(
        args.model.resolve(), args.output.absolute(),
        vision_masked=args.vision_masked,
        mtp_drafter=args.mtp_drafter,
        cpu_draft=args.cpu_draft,
    )
