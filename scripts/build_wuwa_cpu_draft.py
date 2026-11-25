"""Build the CPU draft runtime from the same Qwen3.5-0.8B weights used by MLX."""

import argparse
import json
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from wuwa_coreml import source_fingerprints

RUNTIME = "llama.cpp b12441"
TENSOR_NAMES = {
    "attn_norm.weight": "input_layernorm.weight",
    "post_attention_norm.weight": "post_attention_layernorm.weight",
    "ffn_gate.weight": "mlp.gate_proj.weight",
    "ffn_up.weight": "mlp.up_proj.weight",
    "ffn_down.weight": "mlp.down_proj.weight",
    "attn_q.weight": "self_attn.q_proj.weight",
    "attn_k.weight": "self_attn.k_proj.weight",
    "attn_v.weight": "self_attn.v_proj.weight",
    "attn_output.weight": "self_attn.o_proj.weight",
    "attn_q_norm.weight": "self_attn.q_norm.weight",
    "attn_k_norm.weight": "self_attn.k_norm.weight",
    "attn_qkv.weight": "linear_attn.in_proj_qkv.weight",
    "attn_gate.weight": "linear_attn.in_proj_z.weight",
    "ssm_beta.weight": "linear_attn.in_proj_b.weight",
    "ssm_alpha.weight": "linear_attn.in_proj_a.weight",
    "ssm_a": "linear_attn.A_log",
    "ssm_conv1d.weight": "linear_attn.conv1d.weight",
    "ssm_dt.bias": "linear_attn.dt_bias",
    "ssm_norm.weight": "linear_attn.norm.weight",
    "ssm_out.weight": "linear_attn.out_proj.weight",
}


def float_weights(tensor: NDArray[np.generic]) -> NDArray[np.float32]:
    if tensor.dtype == np.uint16:  # BF16 stored as its raw bits
        return (tensor.astype(np.uint32) << 16).view(np.float32)
    return tensor.astype(np.float32, copy=False)


def pack_q4(
    weight: NDArray[np.generic], scale: NDArray[np.float32], bias: NDArray[np.float32],
) -> NDArray[np.uint8]:
    rows, words = weight.shape
    blocks = words // 4
    if words % 8 or scale.shape != (rows, blocks // 2) or bias.shape != scale.shape:
        raise ValueError("Expected MLX 4-bit affine weights with groups of 64")
    quantized = weight.view(np.uint8).reshape(rows, blocks, 16)
    packed = np.empty((rows, blocks, 20), dtype=np.uint8)
    # Q4_1 has a scale and bias for each half of an MLX group of 64.
    for start in range(0, rows, 4096):
        end = start + 4096
        low, high = np.split(quantized[start:end], 2, axis=-1)
        packed[start:end, :, 4::2] = (low & 15) | (high << 4)
        packed[start:end, :, 5::2] = (low >> 4) | (high & 240)
    for index, parameter in enumerate((scale, bias)):
        half = np.repeat(parameter.astype(np.float16), 2, axis=-1)
        packed[:, :, 2 * index:2 * index + 2] = half.view(np.uint8).reshape(rows, blocks, 2)
    return packed.reshape(rows, -1)


def export_weights(model: Path, template: Path, output: Path) -> None:
    from gguf import GGMLQuantizationType, GGUFReader, GGUFValueType, GGUFWriter

    with (model / "model.safetensors").open("rb") as descriptor:
        (header_size,) = struct.unpack("<Q", descriptor.read(8))
        header = json.loads(descriptor.read(header_size))
    data = np.memmap(model / "model.safetensors", mode="r", dtype=np.uint8, offset=8 + header_size)

    def tensor(name: str) -> NDArray[np.generic]:
        layout = header["language_model.model." + name]
        start, end = layout["data_offsets"]
        dtype = {"U32": np.uint32, "BF16": np.uint16, "F32": np.float32}[layout["dtype"]]
        return data[start:end].view(dtype).reshape(layout["shape"])

    reference = GGUFReader(str(template))
    architecture = reference.get_field("general.architecture")
    if architecture is None or architecture.contents() != "qwen35":
        raise ValueError("Expected a Qwen3.5-0.8B GGUF metadata template")
    writer = GGUFWriter(output, "qwen35", use_temp_file=True)
    try:
        for name, field in reference.fields.items():
            if name.startswith(("GGUF.", "general.")) or not field.types:
                continue
            subtype = field.types[1] if field.types[0] == GGUFValueType.ARRAY else None
            writer.add_key_value(name, field.contents(), field.types[0], subtype)
        writer.add_name("BoxAgent Qwen3.5 0.8B affine weights")
        writer.add_file_type(3)
        writer.add_quantization_version(2)
        for entry in reference.tensors:
            name = entry.name
            if name.startswith("blk."):
                _, index, layer = name.split(".", 2)
                source = f"layers.{index}.{TENSOR_NAMES[layer]}"
            else:
                source = {"token_embd.weight": "embed_tokens.weight",
                          "output_norm.weight": "norm.weight"}[name]
            weight = tensor(source)
            if weight.dtype == np.uint32:
                stem = source.removesuffix("weight")
                packed = pack_q4(
                    weight, float_weights(tensor(stem + "scales")),
                    float_weights(tensor(stem + "biases")),
                )
                writer.add_tensor(name, packed, raw_dtype=GGMLQuantizationType.Q4_1)
            else:
                dense = np.squeeze(float_weights(weight))
                writer.add_tensor(name, -np.exp(dense) if name.endswith(".ssm_a") else dense)
        writer.write_header_to_file()
        writer.write_kv_data_to_file()
        writer.write_tensors_to_file()
    finally:
        writer.close()


def build(model: Path, output: Path, source: Path, libraries: Path, template: Path) -> None:
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Bundle already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vision-draft-", dir=output.parent) as directory:
        staging = Path(directory) / "bundle"
        staging.mkdir()
        for name in ("llama", "ggml-base", "ggml-cpu", "ggml-metal", "ggml-rpc", "ggml-blas"):
            library = f"lib{name}.1.dylib"
            shutil.copy2(libraries / library, staging / library)
        ggml = source / "ggml"
        base = staging / "libggml-base.1.dylib"
        registry = staging / "libggml.1.dylib"
        # The upstream shared libraries are dependencies, with only the CPU backend registered.
        subprocess.run([
            "xcrun", "clang++", "-std=c++17", "-O3", "-dynamiclib", "-DGGML_USE_CPU",
            f"-I{ggml / 'include'}", f"-I{ggml / 'src'}",
            str(ggml / "src/ggml-backend-dl.cpp"), str(ggml / "src/ggml-backend-reg.cpp"),
            str(base), str(staging / "libggml-cpu.1.dylib"),
            "-Wl,-install_name,@rpath/libggml.1.dylib", "-Wl,-rpath,@loader_path",
            "-o", str(registry),
        ], check=True)
        subprocess.run([
            "xcrun", "clang", "-Wall", "-Wextra", "-O3", "-dynamiclib",
            f"-I{source / 'include'}", f"-I{ggml / 'include'}",
            str(Path(__file__).with_name("wuwa_cpu_draft_bridge.c")),
            str(staging / "libllama.1.dylib"), str(registry), str(base),
            "-Wl,-rpath,@loader_path", "-framework", "CoreFoundation", "-framework", "IOKit",
            "-o", str(staging / "libwuwa_cpu_draft.dylib"),
        ], check=True)
        export_weights(model, template, staging / "weights.gguf")
        manifest = {"version": 1, "runtime": RUNTIME, "source_sha256": source_fingerprints(model)}
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        staging.rename(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--llama-source", type=Path, required=True, help="llama.cpp b12441 source")
    parser.add_argument("--llama-libs", type=Path, required=True, help="b12441 ARM64 libraries")
    parser.add_argument("--gguf-template", type=Path, required=True, help="Qwen3.5-0.8B text GGUF")
    args = parser.parse_args()
    build(args.model, args.output, args.llama_source, args.llama_libs, args.gguf_template)
