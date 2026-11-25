"""Share Qwen image prefill and speculative decoding across the vision worker."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from importlib import import_module
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any, cast

import mlx.core as mx
import numpy as np
from mlx_vlm.generate.dispatch import generate
from mlx_vlm.prompt_utils import apply_chat_template
from numpy.typing import NDArray

if TYPE_CHECKING:
    from wuwa_cpu_draft import WuwaCpuDraft

SEQ = 1088
HIDDEN = 1024
CONTEXT = 2048
MIXED = 6144
EXTRA = 2080
SCENES = {"fighting", "settled", "unclear"}
REVIEW_PREFIX = '{"scene":"'


class TensorBuffer(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]


def buffers(tensors: dict[str, Any]) -> Any:
    return (TensorBuffer * len(tensors))(
        *(
            TensorBuffer(name.encode(), tensor.ctypes.data, tensor.size)
            for name, tensor in tensors.items()
        )
    )


def pack(array: Any) -> Any:
    length, channels = array.shape[1:]
    tensor = np.zeros((1, channels, 1, SEQ), dtype=np.float16)
    tensor[0, :, 0, :length] = np.asarray(array.astype(mx.float16))[0].T
    return tensor


def valid_review(text: str) -> bool:
    try:
        payload = json.loads(text)
    except ValueError:
        return False
    return (
        isinstance(payload, dict)
        and isinstance(scene := payload.get("scene"), str)
        and scene in SCENES
    )


def draft_token_ids(tokenizer: Any, vocab_size: int) -> list[int]:
    # Only the predictor uses this subset. The target still scores every vocabulary row.
    tokens = set(range(min(4096, vocab_size)))
    fields = ("scene", "observation", "observed_frames", "praise", "detail", "frames", "Frame")
    for key in (*SCENES, *fields):
        tokens.update(tokenizer.encode(key, add_special_tokens=False))
    end = min(vocab_size, len(tokenizer))
    for start in range(4096, end, 8192):
        batch = [[index] for index in range(start, min(start + 8192, end))]
        words = tokenizer.backend_tokenizer.decode_batch(batch, skip_special_tokens=False)
        tokens.update(
            index for index, word in enumerate(words, start)
            if any("\u3400" <= char <= "\u9fff" or char in "，。；！：、（）「」" for char in word)
        )
    return sorted(index for index in tokens if index < vocab_size)


class WuwaTextPrefill:
    def __init__(
        self,
        model: Any,
        directory: Path,
        fingerprints: dict[str, str],
        cancelled: Event,
        parameters: dict[int, tuple[Any, ...]],
    ) -> None:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if (
            manifest.get("version") != 1
            or manifest.get("sequence_length") != SEQ
            or manifest.get("source_sha256") != fingerprints
        ):
            raise ValueError("Invalid Wuwa language prefill bundle for this model")
        self.language = model.language_model
        self.transformer = self.language.model
        self.layers = self.transformer.layers
        if len(self.layers) != 24 or any(
            layer.is_linear != (i % 4 != 3) for i, layer in enumerate(self.layers)
        ):
            raise ValueError("The Wuwa language tower has an unexpected layer layout")
        self.stages: dict[int, int] = {}
        self.library = ctypes.CDLL(str(directory / "libwuwa_text.dylib"))
        open_model = self.library.boxagent_wuwa_text_open
        open_model.argtypes = [ctypes.c_char_p]
        open_model.restype = ctypes.c_void_p
        self.release = self.library.boxagent_wuwa_text_close
        self.release.argtypes = [ctypes.c_void_p]
        self.release.restype = None
        self.predict = self.library.boxagent_wuwa_text_predict
        self.predict.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(TensorBuffer),
            ctypes.c_size_t,
            ctypes.POINTER(TensorBuffer),
            ctypes.c_size_t,
        ]
        self.predict.restype = ctypes.c_int
        self.filter = self.library.boxagent_wuwa_text_depthwise
        self.filter.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        self.filter.restype = None
        self.linear = self.library.boxagent_wuwa_text_linear
        self.linear.argtypes = [ctypes.c_void_p] * 5 + [ctypes.c_size_t] + [ctypes.c_void_p] * 2
        self.linear.restype = ctypes.c_int
        self.parameters = parameters
        self.cpu_draft: WuwaCpuDraft | None = None
        try:
            for i in range(len(self.layers)):
                if cancelled.is_set():
                    raise RuntimeError("Wuwa text prefill closed during loading")
                if i == 0 or self.layers[i - 1].is_linear:
                    path = directory / f"part-{i:02}.mlmodelc"
                    handle = open_model(os.fsencode(path))
                    if not handle:
                        raise RuntimeError(f"Could not open Wuwa language stage {i}")
                    self.stages[i] = handle
        except BaseException:
            self.close()
            raise
        draft_directory = directory / "mtp"
        self.draft_directory = (
            draft_directory if (draft_directory / "model.safetensors").is_file() else None
        )
        self.drafter: Any | None = None
        self.reported_cpu_draft = False
        self.verify_logits: Callable[[mx.array], mx.array] | None = None
        self.verify_tokens: Callable[[mx.array], mx.array] | None = None
        self.reported = False
        self.prefill_failed = False

    def close(self) -> None:
        if self.cpu_draft is not None:
            self.cpu_draft.close()
            self.cpu_draft = None
        for handle in self.stages.values():
            self.release(handle)
        self.stages.clear()

    def warmup(self, cancelled: Event) -> None:
        hidden = np.full((1, HIDDEN, 1, SEQ), 0.05, dtype=np.float16)
        context = np.full((1, CONTEXT, 1, SEQ), 0.01, dtype=np.float16)
        next_hidden = np.empty_like(hidden)
        mixed = np.empty((SEQ, MIXED), dtype=np.float16)
        extra = np.empty((SEQ, EXTRA), dtype=np.float16)
        cosine = np.ones((1, 64, 1, SEQ), dtype=np.float16)
        sine = np.zeros_like(cosine)
        key, value = (np.empty((1, 512, 1, SEQ), dtype=np.float16) for _ in range(2))
        for index in self.stages:
            if cancelled.is_set():
                raise RuntimeError("Wuwa text prefill closed during warmup")
            source: dict[str, NDArray[np.float16]] = {"hidden": hidden}
            destination: dict[str, NDArray[np.float16]] = {"hidden_out": next_hidden}
            if index:
                source["context"] = context
            full = not self.layers[index].is_linear
            if index + int(full) < len(self.layers):
                destination.update(mixed=mixed, extra=extra)
            if full:
                source.update(cosine=cosine, sine=sine)
                destination.update(key=key, value=value)
            self._stage(index, source, destination)

    def _stage(self, index: int, inputs: dict[str, Any], outputs: dict[str, Any]) -> None:
        code = self.predict(
            self.stages[index], buffers(inputs), len(inputs), buffers(outputs), len(outputs)
        )
        if code:
            raise RuntimeError(f"Wuwa language stage {index} failed")

    def _prefill(self, embeds: Any, positions: Any, cache: list[Any]) -> Any:
        length = embeds.shape[1]
        hidden = pack(embeds)
        next_hidden = np.empty_like(hidden)
        context = np.zeros((1, CONTEXT, 1, SEQ), dtype=np.float16)
        mixed = np.empty((SEQ, MIXED), dtype=np.float16)
        extra = np.empty((SEQ, EXTRA), dtype=np.float16)
        filtered = np.empty((length, MIXED), dtype=np.float16)
        gated = np.empty((length, CONTEXT), dtype=np.float16)
        key, value = (np.empty((1, 512, 1, SEQ), dtype=np.float16) for _ in range(2))
        cos, sin = self.layers[3].self_attn.rotary_emb(embeds, positions)
        rotary = {"cosine": pack(cos), "sine": pack(sin)}
        self._stage(
            0, {"hidden": hidden}, {"hidden_out": next_hidden, "mixed": mixed, "extra": extra}
        )
        hidden, next_hidden = next_hidden, hidden

        for index, (decay, dt, norm, weights) in self.parameters.items():
            entry = cache[index]
            entry[0] = mx.array(mixed[None, length - 3 : length].copy()).astype(mx.bfloat16)
            state = np.empty((1, 16, 128, 128), dtype=np.float32)
            self.filter(mixed.ctypes.data, weights.ctypes.data, length, filtered.ctypes.data)
            inputs = (filtered, extra, decay, dt, norm)
            code = self.linear(
                *(item.ctypes.data for item in inputs), length, state.ctypes.data,
                gated.ctypes.data,
            )
            if code:
                raise RuntimeError(f"Wuwa recurrent attention failed at stage {index}")
            entry[1] = mx.array(state)
            if hasattr(entry, "advance"):
                entry.advance(length)
            context[0, :, 0, :length] = gated.T

            following = index + 1
            has_attention = not self.layers[following].is_linear
            source = {"hidden": hidden, "context": context}
            destination = {"hidden_out": next_hidden}
            if following + int(has_attention) < len(self.layers):
                destination.update(mixed=mixed, extra=extra)
            if has_attention:
                source.update(rotary)
                destination.update(key=key, value=value)
            self._stage(following, source, destination)
            hidden, next_hidden = next_hidden, hidden
            if has_attention:
                arrays = [
                    np.ascontiguousarray(
                        item[0, :, 0, :length].reshape(2, 256, length).transpose(0, 2, 1)
                    )[None]
                    for item in (key, value)
                ]
                cache[following].update_and_fetch(
                    *(mx.array(item).astype(mx.bfloat16) for item in arrays)
                )
        values = np.ascontiguousarray(hidden[0, :, 0, :length].T)[None]
        if not np.isfinite(values).all():
            raise RuntimeError("Wuwa language prefill returned non-finite values")
        return self.transformer.norm(mx.array(values).astype(mx.bfloat16))

    def _load_drafter(self, model: Any, processor: Any) -> None:
        directory, self.draft_directory = self.draft_directory, None
        if directory is None:
            return
        try:
            from mlx_vlm.speculative.drafters import load_drafter, validate_drafter_compatibility
            from mlx_vlm.speculative.drafters.qwen3_5_mtp import Qwen3_5MTPDraftModel
            from wuwa_cpu_draft import greedy_tokens, qwen_verification_head

            if not (directory / "config.json").is_file():
                raise ValueError("MTP is missing its configuration")
            drafter, kind = load_drafter(str(directory), "mtp")
            if (
                kind != "mtp" or not isinstance(drafter, Qwen3_5MTPDraftModel)
                or drafter.config.model_type != "qwen3_5_mtp"
            ):
                raise ValueError("Expected a Qwen3.5 MTP head")
            validate_drafter_compatibility(model, drafter, kind)

            head, full_logits = qwen_verification_head(self.language)
            candidates = draft_token_ids(processor.tokenizer, head.weight.shape[0])
            ids = mx.array(candidates, dtype=mx.uint32)
            weights = mx.take(head.weight, ids, axis=0)
            scales = mx.take(head.scales, ids, axis=0)
            biases = mx.take(head.biases, ids, axis=0)
            mx.eval(weights, scales, biases)

            def predict(hidden: mx.array) -> mx.array:
                logits = mx.quantized_matmul(
                    hidden, weights, scales, biases, transpose=True,
                    group_size=64, bits=4, mode="affine",
                )
                return ids[mx.argmax(logits, axis=-1)]

            def verify(hidden: mx.array) -> mx.array:
                return greedy_tokens(full_logits(hidden))

            drafter._greedy_token = predict
            self.verify_logits = full_logits
            self.verify_tokens = verify
            self.drafter = drafter
        except (
            AttributeError, ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError,
        ) as exc:
            print(f"Wuwa MTP unavailable; using standard decode: {exc}", file=sys.stderr)
        else:
            print("Wuwa MTP ready with full vocabulary verification", file=sys.stderr)

    @contextmanager
    def _metal_repetition(self, penalty: float | None, context: int) -> Iterator[None]:
        if not penalty:
            yield
            return
        from wuwa_cpu_draft import greedy_tokens, penalize_repeated_tokens

        logits_from_hidden = self.verify_logits
        if logits_from_hidden is None:
            raise RuntimeError("Missing Qwen verification logits")
        mtp: Any = import_module("mlx_vlm.speculative.mtp")
        speculative: Any = import_module("mlx_vlm.speculative.utils")
        original_rounds = speculative._mtp_rounds
        original_verify = mtp._mtp_verify_target

        def rounds(*args: Any, **kwargs: Any) -> Iterator[tuple[int, Any]]:
            # mlx-vlm passes the IDs that contributed to its first sampled token.
            ids = kwargs["prompt_tokens"]
            history = cast(list[int], ids.flatten().tolist())[-context:]
            history.append(int(kwargs["first_bonus"]))

            def verify(lm: Any, block: mx.array, cache: Any, sampler: Any,
                       *, sample_target_tokens: bool = True) -> Any:
                result = original_verify(lm, block, cache, sampler, sample_target_tokens=False)
                tokens = cast(list[int], block.flatten().tolist())
                penalized = penalize_repeated_tokens(
                    logits_from_hidden(result.hidden), history[:-1], tokens, penalty, context,
                )
                result.target_tokens = greedy_tokens(penalized)
                return result

            mtp._mtp_verify_target = verify
            try:
                with closing(original_rounds(*args, **kwargs)) as stream:
                    for token, logprobs in stream:
                        history.append(token)
                        yield token, logprobs
            finally:
                mtp._mtp_verify_target = original_verify

        speculative._mtp_rounds = rounds
        try:
            yield
        finally:
            speculative._mtp_rounds = original_rounds
            mtp._mtp_verify_target = original_verify

    def analyze_image(
        self, model: Any, processor: Any, config: dict[str, Any], image: str, prompt: str,
        generation: dict[str, Any], *, structured: bool, pressured: bool,
    ) -> str | None:
        from wuwa_cpu_draft import supports_greedy_speculation

        formatted = apply_chat_template(
            processor, config, prompt, num_images=1, enable_thinking=False,
        )
        if not isinstance(formatted, str):
            return None
        prefix = REVIEW_PREFIX if structured else ""
        formatted += prefix
        eager = type(self.transformer)
        stock = eager.__call__
        dispatch: Any = import_module("mlx_vlm.generate.dispatch")
        stream_generate = dispatch.stream_generate
        generation = {**generation, "prefill_step_size": SEQ}
        used = False

        def call(
            instance: Any,
            inputs: Any,
            inputs_embeds: Any = None,
            mask: Any = None,
            cache: Any = None,
            position_ids: Any = None,
            capture_layer_ids: Any = None,
            hidden_sink: Any = None,
        ) -> Any:
            nonlocal used
            if (
                not self.prefill_failed
                and instance is self.transformer
                and inputs_embeds is not None
                and inputs.shape[0] == 1
                and (3 if pressured else 850) <= inputs.shape[1] <= SEQ
                and position_ids is not None
                and mask is None
                and hidden_sink is None
                and cache is not None
                and cache[instance.fa_idx].offset == 0
                and cache[instance.ssm_idx][0] is None
            ):
                pending = self.language.make_cache()
                try:
                    with mx.stream(mx.cpu):
                        output = self._prefill(inputs_embeds, position_ids, pending)
                        mx.eval([entry.state for entry in pending])
                except (OSError, RuntimeError, ValueError) as exc:
                    self.prefill_failed = True
                    print(f"Vision text prefill failed; using MLX: {exc}", file=sys.stderr)
                else:
                    cache[:] = pending
                    used = True
                    return output
            return stock(
                instance,
                inputs,
                inputs_embeds,
                mask,
                cache,
                position_ids,
                capture_layer_ids,
                hidden_sink,
            )

        def until_review(*args: Any, **kwargs: Any) -> Iterator[Any]:
            text = REVIEW_PREFIX
            with closing(stream_generate(*args, **kwargs)) as stream:
                for item in stream:
                    text += item.text
                    yield item
                    if "}" in item.text and valid_review(text):
                        break

        def decode(**options: Any) -> Any:
            return generate(model, processor, formatted, image=[image], verbose=False, **options)

        answer = None
        eager.__call__ = call
        if structured:
            dispatch.stream_generate = until_review
        try:
            cpu = self.cpu_draft
            if pressured and cpu is not None:
                try:
                    answer = cpu.generate_vision(
                        generate, model, processor, formatted, image, generation, pressured=True,
                    )
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    cpu.close()
                    self.cpu_draft = None
                    print(f"Wuwa CPU draft failed; using Metal draft: {exc}", file=sys.stderr)
                else:
                    if answer is not None and cpu.used_cpu_draft and not self.reported_cpu_draft:
                        print("Wuwa CPU draft verified by the full Metal target", file=sys.stderr)
                        self.reported_cpu_draft = True
            if answer is None and supports_greedy_speculation(generation):
                self._load_drafter(model, processor)
                if self.drafter is not None and self.verify_tokens is not None:
                    original = self.language.speculative_argmax_from_hidden
                    self.language.speculative_argmax_from_hidden = self.verify_tokens
                    try:
                        with self._metal_repetition(
                            generation.get("repetition_penalty"),
                            generation.get("repetition_context_size", 64),
                        ):
                            answer = decode(**generation, draft_model=self.drafter,
                                            draft_kind="mtp", draft_block_size=3)
                    except (AttributeError, RuntimeError, ValueError) as exc:
                        self.drafter = None
                        print(f"Wuwa MTP failed; using standard decode: {exc}", file=sys.stderr)
                    finally:
                        self.language.speculative_argmax_from_hidden = original
            if answer is None:
                answer = decode(**generation)
        finally:
            dispatch.stream_generate = stream_generate
            eager.__call__ = stock
        text = (prefix + answer.text).strip()
        if used and not self.reported:
            print("Local vision used Neural Engine and CPU language prefill", file=sys.stderr)
            self.reported = True
        return text if not structured or valid_review(text) else None


class WuwaPrefillStartup:
    """Keep the normal worker available while loading and warming the optional stages."""

    def __init__(self, model: Any, directory: Path, fingerprints: dict[str, str]) -> None:
        if mx.default_device() != mx.gpu or mx.device_info().get("architecture") != "applegpu_g15s":
            raise ValueError("Wuwa text prefill requires the tested Apple GPU architecture")
        parameters = {}
        # MLX changes its default device inside stream scopes. Finish this small
        # copy before serving requests and keep background initialization native.
        with mx.stream(mx.cpu):
            for index, layer in enumerate(model.language_model.model.layers):
                if layer.is_linear:
                    attention = layer.linear_attn
                    weights = (
                        mx.exp(attention.A_log.astype(mx.float32)),
                        attention.dt_bias.astype(mx.float32),
                        attention.norm.weight.astype(mx.float32),
                        attention.conv1d.weight[:, :, 0].T.astype(mx.float32),
                    )
                    parameters[index] = tuple(np.ascontiguousarray(weight) for weight in weights)
        self.cancelled = Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wuwa-prefill")
        self.future = self._executor.submit(self._load, model, directory, fingerprints, parameters)

    def _load(
        self,
        model: Any,
        directory: Path,
        fingerprints: dict[str, str],
        parameters: dict[int, tuple[Any, ...]],
    ) -> WuwaTextPrefill | None:
        prefill = None
        try:
            prefill = WuwaTextPrefill(model, directory, fingerprints, self.cancelled, parameters)
            prefill.warmup(self.cancelled)
        except (AttributeError, OSError, RuntimeError, ValueError) as exc:
            if prefill is not None:
                prefill.close()
            if not self.cancelled.is_set():
                print(f"Wuwa text prefill unavailable; using MLX: {exc}", file=sys.stderr)
            return None
        cpu_directory = directory / "cpu-draft"
        if not self.cancelled.is_set() and (cpu_directory / "manifest.json").is_file():
            try:
                from wuwa_cpu_draft import WuwaCpuDraft

                prefill.cpu_draft = WuwaCpuDraft(cpu_directory, fingerprints)
                print("Wuwa CPU draft ready", file=sys.stderr)
            except (AttributeError, ImportError, OSError, RuntimeError, ValueError) as exc:
                print(f"Wuwa CPU draft unavailable; using Metal draft: {exc}", file=sys.stderr)
        print("Wuwa text prefill is ready", file=sys.stderr)
        return prefill

    def close(self) -> None:
        self.cancelled.set()
        self._executor.shutdown(cancel_futures=True)
        if not self.future.cancelled() and (prefill := self.future.result()) is not None:
            prefill.close()
