"""Draft Qwen vision responses on the CPU and verify them on Metal."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import struct
import sys
from collections import OrderedDict
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import closing
from dataclasses import dataclass
from importlib import import_module
from io import BytesIO
from pathlib import Path
from threading import Event
from time import sleep
from typing import Any, cast

import mlx.core as mx
import numpy as np

# The pinned CPU runtime imports a separate copy of the target model's cache.
_SEQUENCE_MAGIC = 0xAF143CD8
_CONTEXT_CAPACITY = 1536
_FIRST_BLOCK = 3
_BLOCK = 8
# GPU telemetry moves between samples even while the renderer has work queued.
_GPU_PRESSURE_PERCENT = 90
_GREEDY_OPTIONS = {
    "max_tokens", "temperature", "prefill_step_size", "repetition_penalty",
    "repetition_context_size",
}


def source_fingerprints(model_path: Path) -> dict[str, str]:
    values = {}
    for name in ("model.safetensors", "config.json"):
        with (model_path / name).open("rb") as source:
            values[name] = hashlib.file_digest(source, "sha256").hexdigest()
    return values


def supports_greedy_speculation(options: dict[str, Any]) -> bool:
    if options.get("temperature") != 0.0 or options.keys() - _GREEDY_OPTIONS:
        return False
    context = options.get("repetition_context_size", 64)
    return not options.get("repetition_penalty") or (isinstance(context, int) and context > 0)


def qwen_verification_head(language: Any) -> tuple[Any, Callable[[mx.array], mx.array]]:
    from mlx.nn import QuantizedEmbedding, QuantizedLinear
    from mlx_vlm.models.qwen3_5.speculative_verifier import Qwen3_5ExactSpeculativeVerifier

    embedding = language.model.embed_tokens
    if (
        not language.args.tie_word_embeddings
        or not isinstance(embedding, QuantizedEmbedding)
        or (embedding.group_size, embedding.bits, embedding.mode) != (64, 4, "affine")
        or embedding.biases is None
    ):
        raise ValueError("Expected the tied Qwen3.5 affine 4-bit output weights")
    head = QuantizedLinear(
        embedding.weight.shape[1] * 8, 8, bias=False, group_size=64, bits=4, mode="affine",
    )
    head.weight = embedding.weight
    head.scales = embedding.scales
    head.biases = embedding.biases
    verifier = Qwen3_5ExactSpeculativeVerifier()
    if not verifier.can_quantized_head(head):
        raise ValueError("The full vocabulary verifier cannot use these weights")

    from vision_projection import matrix_verifier

    try:
        matrix = matrix_verifier()
    except (AttributeError, RuntimeError, ValueError) as exc:
        matrix = None
        print(f"Matrix vocabulary projection unavailable: {exc}", file=sys.stderr)

    def logits(hidden: mx.array) -> mx.array:
        nonlocal matrix
        if matrix is not None:
            try:
                projected = matrix(head, hidden)
            except (RuntimeError, ValueError) as exc:
                matrix = None
                print(f"Matrix vocabulary projection unavailable: {exc}", file=sys.stderr)
            else:
                if projected is not None:
                    return projected
        output = verifier.quantized_linear(head, hidden)
        if output is None:
            raise RuntimeError("Qwen verifier does not support the current token block")
        return output

    return head, logits


def greedy_tokens(logits: mx.array) -> mx.array:
    # Match greedy sampling from the BF16 log-probabilities used by mlx-vlm.
    return mx.argmax(logits - mx.logsumexp(logits, axis=-1, keepdims=True), axis=-1)


def penalize_repeated_tokens(
    logits: mx.array, history: list[int], block: list[int], penalty: float, context: int,
) -> mx.array:
    tokens = history[-context:] + block
    rows = []
    for end in range(len(tokens) - len(block) + 1, len(tokens) + 1):
        recent = tokens[max(0, end - context):end]
        rows.append(recent + [recent[0]] * (context - len(recent)))
    indices = mx.array([rows], dtype=mx.int32)
    original = mx.take_along_axis(logits, indices, axis=-1)
    adjusted = mx.where(original < 0, original * penalty, original / penalty)
    return mx.put_along_axis(logits, indices, adjusted, axis=-1)


def cpu_cache(cache: list[Any], positions: mx.array, tokens: mx.array) -> bytes:
    """Encode the batch-one Qwen3.5 cache for llama.cpp b12441."""
    length = int(positions.shape[-1])
    if (
        positions.shape != (3, 1, length)
        or tokens.shape != (1, length)
        or len(cache) != 24
        or not 0 < length < _CONTEXT_CAPACITY
    ):
        raise ValueError("Unexpected Wuwa draft cache layout")
    attention = cache[3::4]
    recurrent = [entry for i, entry in enumerate(cache) if i % 4 != 3]
    time, y, x = np.asarray(positions).reshape(3, length)
    metadata = np.empty((length, 6), dtype=np.int32)
    metadata[:, 0] = time
    metadata[:, 1] = 1  # one sequence per cell
    metadata[:, 2] = x
    metadata[:, 3] = y
    metadata[:, 4] = np.asarray(tokens).reshape(length)
    metadata[:, 5] = 0  # sequence ID

    with BytesIO() as state:
        state.write(struct.pack("<IIII", _SEQUENCE_MAGIC, 0, 1, length))
        state.write(metadata.tobytes())
        state.write(struct.pack("<IIII", 0, 6, 0, 0))  # K/V are both untransposed
        for field in (0, 1):
            for entry in attention:
                array = entry.state[field]
                if array is None or array.shape != (1, 2, length, 256):
                    raise ValueError("Unexpected Wuwa draft attention cache")
                array = array.transpose(0, 2, 1, 3).astype(mx.float16)
                state.write(struct.pack("<iQ", 1, 1024))  # GGML F16 and 512 elements
                state.write(np.asarray(array).tobytes())

        state.write(struct.pack("<IiIII", 1, int(time[-1]), 0, 0, 24))
        for field, shape in ((0, (1, 3, 6144)), (1, (1, 16, 128, 128))):
            for entry in recurrent:
                array = entry[field]
                if array is None or array.shape != shape:
                    raise ValueError("Unexpected Wuwa draft recurrent cache")
                if field == 0:
                    array = array.transpose(0, 2, 1)  # convolution time is contiguous in GGML
                data = np.asarray(array.astype(mx.float32))
                state.write(struct.pack("<iQ", 0, data.nbytes))  # GGML F32
                state.write(data.tobytes())
        return state.getvalue()


@dataclass
class ReviewCheckpoint:
    """Prefill state shared only by the two format attempts on one image."""

    deferred_first_reply: bool = False
    tokens: tuple[int, ...] = ()
    positions: mx.array | None = None
    rope_deltas: mx.array | None = None
    layers: tuple[tuple[mx.array, ...], ...] = ()
    started_under_gpu_pressure: bool | None = None

    def watch_opening(self, tokenizer: Any, tokens: list[int]) -> bool:
        opening = tokenizer.decode(tokens).lstrip()
        if opening.startswith("```"):
            self.deferred_first_reply = True
            return False
        return len(tokens) < 8 and "```".startswith(opening)

    def remember(
        self, ids: mx.array, positions: mx.array, rope_deltas: mx.array, cache: list[Any],
    ) -> None:
        self.tokens = tuple(ids[0].tolist())
        self.positions, self.rope_deltas = positions, rope_deltas
        # ArraysCache.state is a mutable list. Retain the arrays before decoding advances it.
        self.layers = tuple(tuple(entry.state) for entry in cache)

    def resume(self, ids: mx.array, model: Any, cache: list[Any]) -> mx.array | None:
        if self.positions is None or self.rope_deltas is None or ids.shape[1] < len(self.tokens):
            return None
        current = tuple(ids[0].tolist())
        size = len(self.tokens)
        appended = current[size:]
        if (
            current[:size] != self.tokens or len(cache) != len(self.layers)
            or model.config.image_token_index in appended
            or model.config.video_token_index in appended
        ):
            return None
        for entry, state in zip(cache, self.layers, strict=True):
            entry.state = list(state)
        if not appended:
            # A failed format retry needs the original prompt and its original seed again.
            model.language_model._rope_deltas = self.rope_deltas
            return self.positions
        # The first attempt consumed the entire prompt except its final seed token.
        offset = size - 1
        positions = mx.concatenate([
            self.positions[..., :offset],
            self.positions[..., offset:] + mx.arange(ids.shape[1] - offset),
        ], axis=-1)
        suffix = ids[:, offset:-1]
        language = model.language_model
        language(suffix, inputs_embeds=language.model.embed_tokens(suffix), cache=cache,
                 position_ids=positions, rope_deltas=self.rope_deltas, skip_logits=True)
        return positions


def clear_winners(logits: mx.array, selected: mx.array) -> mx.array:
    """Leave ties and near-ties to the usual short-block decoder."""
    top = mx.take_along_axis(logits, selected[..., None], axis=-1).astype(mx.float32)
    margin = mx.maximum(0.125, mx.abs(top) / 64)
    nearby = mx.sum(logits >= (top - margin).astype(logits.dtype), axis=-1)
    return (nearby == 1) & mx.isfinite(top[..., 0])


def repeated_tail(tokens: list[int], remaining: int) -> list[int]:
    """Propose a repeated suffix without consulting any other request."""
    for width in range(1, min(48, (len(tokens) - 3) // 2) + 1):
        match = max(7, width)
        if tokens[-match:] == tokens[-width - match:-width]:
            pattern = tokens[-width:]
            return (pattern * ((remaining + width - 1) // width))[:remaining]
    return []


def matches_target_tail(
    language: Any, cache: list[Any], seed: int, proposal: list[int], dtype: Any,
    history: list[int], penalty: float | None, context: int,
) -> bool:
    # Each recurrent layer owns a mutable state list, and each attention cache owns
    # its offset. Only immutable MLX arrays may be shared with the active decoder.
    shadow = language.make_cache()
    for target, source in zip(shadow, cache, strict=True):
        target.state = list(source.state)
    inputs = [seed, *proposal[:-1]]
    # Give the renderer a scheduling point between long full-vocabulary projections.
    for i in range(0, len(inputs), 64):
        block = inputs[i:i + 64]
        logits = language(mx.array([block], dtype=dtype), cache=shadow).logits
        if penalty:
            logits = penalize_repeated_tokens(logits, history + inputs[:i], block, penalty, context)
        expected = mx.array([proposal[i:i + len(block)]], dtype=mx.int32)
        selected = greedy_tokens(logits)
        if not mx.all((selected == expected) & clear_winners(logits, selected)).item():
            return False
    return True


class WuwaCpuDraft:
    def __init__(self, directory: Path, fingerprints: dict[str, str]) -> None:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("version") != 1
            or manifest.get("runtime") != "llama.cpp b12441"
            or manifest.get("source_sha256") != fingerprints
        ):
            raise ValueError("CPU draft bundle does not match this Qwen model")

        self.library = ctypes.CDLL(os.fspath(directory / "libwuwa_cpu_draft.dylib"))
        open_model = self.library.boxagent_draft_open
        open_model.argtypes = (ctypes.c_char_p,)
        open_model.restype = ctypes.c_void_p
        self.release = self.library.boxagent_draft_close
        self.release.argtypes = (ctypes.c_void_p,)
        self.release.restype = None
        self.token = self.library.boxagent_draft_token
        self.token.argtypes = (ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32)
        self.token.restype = ctypes.c_int32
        self.penalized_token = getattr(self.library, "boxagent_draft_token_penalized", None)
        if self.penalized_token is not None:
            self.penalized_token.argtypes = (
                ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32,
                ctypes.c_float, ctypes.POINTER(ctypes.c_int32), ctypes.c_size_t,
            )
            self.penalized_token.restype = ctypes.c_int32
        self.restore = self.library.boxagent_draft_restore
        self.restore.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t)
        self.restore.restype = ctypes.c_size_t
        self.gpu_load = self.library.boxagent_draft_gpu_load
        self.gpu_load.argtypes = ()
        self.gpu_load.restype = ctypes.c_int
        self.handle: int | None = open_model(os.fsencode(directory / "weights.gguf"))
        if self.handle is None:
            raise RuntimeError("Could not initialize the Wuwa CPU draft decoder")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wuwa-cpu-draft")
        self.used_cpu_draft = False
        self.previous_gpu_pressure: bool | None = None
        self.vision_logits: Callable[[mx.array], mx.array] | None = None
        self.reported_vision = False
        self.recent_tokens: OrderedDict[str, tuple[int, ...]] = OrderedDict()

    def under_gpu_pressure(self, checkpoint: ReviewCheckpoint | None = None) -> bool:
        if checkpoint is not None and checkpoint.started_under_gpu_pressure is True:
            return True
        first_read = checkpoint is None or checkpoint.started_under_gpu_pressure is None
        load = self.gpu_load()
        if (
            first_read and 0 <= load < _GPU_PRESSURE_PERCENT
            and self.previous_gpu_pressure is not False
        ):
            # A renderer can briefly drain its queue between frames. Confirm a transition to
            # the native decoder after one frame, while steady idle requests can start at once.
            sleep(0.020)
            load = self.gpu_load()
        pressured = load < 0 or load >= _GPU_PRESSURE_PERCENT
        self.previous_gpu_pressure = pressured
        if checkpoint is not None and first_read:
            # Keep high-load retries on one route. Requests started at idle can resample
            # after prefill, just as they did before steering high-load requests.
            checkpoint.started_under_gpu_pressure = pressured
        return pressured

    def close(self) -> None:
        self.executor.shutdown(cancel_futures=True)
        if self.handle is not None:
            self.release(self.handle)
            self.handle = None

    def _sync(self, cache: list[Any], positions: mx.array, tokens: mx.array) -> None:
        with mx.stream(mx.cpu):
            state = cpu_cache(cache, positions, tokens)
        if self.restore(self.handle, state, len(state)) != len(state):
            raise RuntimeError("The CPU draft decoder rejected its prompt cache")

    def _propose(self, position: int, token: int, count: int, cancel: Event) -> list[int]:
        result = []
        for index in range(count):
            if cancel.is_set():
                break
            token = self.token(self.handle, position + index, token)
            if token < 0:
                raise RuntimeError("The CPU draft decoder failed during prediction")
            result.append(token)
        return result

    def _propose_with_penalty(
        self, position: int, token: int, count: int, cancel: Event,
        history: list[int], penalty: float, context: int,
    ) -> list[int]:
        sample = self.penalized_token
        if sample is None:
            return self._propose(position, token, count, cancel)
        result = []
        for index in range(count):
            if cancel.is_set():
                break
            history.append(token)
            recent = tuple(dict.fromkeys(history[-context:]))
            ids = (ctypes.c_int32 * len(recent))(*recent)
            token = sample(self.handle, position + index, token, penalty, ids, len(ids))
            if token < 0:
                raise RuntimeError("The CPU draft decoder failed during prediction")
            result.append(token)
        return result

    def generate_vision(
        self, generate: Callable[..., Any], model: Any, processor: Any, prompt: Any,
        image: str, options: dict[str, Any], *, pressured: bool | None = None,
        checkpoint: ReviewCheckpoint | None = None, check_review_opening: bool = False,
    ) -> Any | None:
        """Use CPU drafts with Metal verification for a compatible image request."""
        self.used_cpu_draft = False
        if (
            self.handle is None or options["max_tokens"] < _FIRST_BLOCK
            or not supports_greedy_speculation(options)
            or not (self.under_gpu_pressure(checkpoint) if pressured is None else pressured)
        ):
            return None
        dispatch: Any = import_module("mlx_vlm.generate.dispatch")
        ar = import_module("mlx_vlm.generate.ar")
        original_steps = dispatch.generate_step
        hint = self.recent_tokens.get(prompt, ()) if isinstance(prompt, str) else ()
        generated: list[int] = []
        watch_opening = check_review_opening and checkpoint is not None
        if (
            watch_opening and hint
            and processor.tokenizer.decode(hint[:1]).lstrip().startswith("```")
        ):
            # Only this verified opening is needed before trying the JSON form.
            hint = hint[:1]
        deferred = False

        def choose_steps(
            ids: mx.array, target: Any, pixels: Any, mask: Any, **kwargs: Any,
        ) -> Iterator[tuple[int, Any]]:
            nonlocal watch_opening, deferred
            length = ids.shape[-1]
            if (
                ids.ndim != 2 or ids.shape[0] != 1 or pixels is None or length < 2
                or length + options["max_tokens"] > _CONTEXT_CAPACITY
            ):
                yield from original_steps(ids, target, pixels, mask, **kwargs)
                return
            if self.vision_logits is None:
                _, self.vision_logits = qwen_verification_head(target.language_model)
            self.used_cpu_draft = True
            with closing(self._steps(
                ids, target, pixels, mask, vision_logits=self.vision_logits,
                prefill_step_size=kwargs.pop("prefill_step_size", ar.DEFAULT_PREFILL_STEP_SIZE),
                hint=hint, checkpoint=checkpoint, **kwargs,
            )) as stream:
                for token, logprobs in stream:
                    generated.append(token)
                    if watch_opening and checkpoint is not None:
                        watch_opening = checkpoint.watch_opening(processor.tokenizer, generated)
                        if checkpoint.deferred_first_reply:
                            # This verified opening cannot parse as a bare object. Let the
                            # usual retry run first, keeping the image state for either outcome.
                            deferred = True
                            yield token, logprobs
                            return
                    yield token, logprobs

        dispatch.generate_step = choose_steps
        try:
            result = generate(model, processor, prompt, image=[image], verbose=False, **options)
        finally:
            dispatch.generate_step = original_steps
        if generated and isinstance(prompt, str):
            # Keep an observed fence as a proposal when there is no earlier complete answer.
            if not deferred or prompt not in self.recent_tokens:
                self.recent_tokens[prompt] = tuple(generated)
            self.recent_tokens.move_to_end(prompt)
            if len(self.recent_tokens) > 4:
                self.recent_tokens.popitem(last=False)
        if self.used_cpu_draft and not self.reported_vision:
            print("Local vision used CPU drafts with Metal verification", file=sys.stderr)
            self.reported_vision = True
        return result

    def _steps(
        self, input_ids: mx.array, model: Any, pixel_values: Any, mask: Any, *,
        max_tokens: int, vision_logits: Callable[[mx.array], mx.array], hint: tuple[int, ...],
        checkpoint: ReviewCheckpoint | None = None,
        prefill_step_size: int | None = None, repetition_penalty: float | None = None,
        repetition_context_size: int = 64, **kwargs: Any,
    ) -> Iterator[tuple[int, None]]:
        stream = import_module("mlx_vlm.generate.ar").generation_stream
        verifier: Any = import_module("mlx_vlm.models.qwen3_5.speculative_verifier")
        delta = import_module("mlx_vlm.models.qwen3_5.gated_delta")
        cache = kwargs.pop("prompt_cache")
        language = model.language_model
        prefix = input_ids[:, :-1]
        with mx.stream(stream):
            positions = checkpoint.resume(input_ids, model, cache) if checkpoint else None
            if positions is None:
                embedded = model.get_input_embeddings(input_ids, pixel_values, mask=mask, **kwargs)
                positions = embedded.position_ids
                chunk_size = prefill_step_size or prefix.shape[1]
                for start in range(0, prefix.shape[1], chunk_size):
                    end = min(start + chunk_size, prefix.shape[1])
                    language(
                        prefix[:, start:end], inputs_embeds=embedded.inputs_embeds[:, start:end],
                        cache=cache, position_ids=positions, rope_deltas=embedded.rope_deltas,
                        skip_logits=True,
                    )
                    if end < prefix.shape[1]:
                        mx.eval([entry.state for entry in cache])
                        mx.clear_cache()
                if checkpoint is not None:
                    checkpoint.remember(input_ids, positions, embedded.rope_deltas, cache)
            first_position = positions[0, 0, -1].item()
            seed = input_ids[0, -1].item()
        if not isinstance(first_position, int) or not isinstance(seed, int):
            raise ValueError("Expected integer token and positional IDs")

        consumed: list[int] = []
        prompt_history: list[int] = (
            cast(list[int], prefix[0, -repetition_context_size:].tolist())
            if repetition_penalty and prefill_step_size is None else []
        )
        offset = 0
        cancel = Event()
        native_penalty = bool(repetition_penalty and self.penalized_token is not None)

        def propose(
            position: int, token: int, count: int, stop: Event, history: list[int],
        ) -> list[int]:
            if native_penalty and repetition_penalty is not None:
                return self._propose_with_penalty(
                    position, token, count, stop, prompt_history + history,
                    repetition_penalty, repetition_context_size,
                )
            return self._propose(position, token, count, stop)

        def sync_cpu() -> None:
            if not consumed:
                self._sync(cache, positions[..., :-1], prefix)
                return
            with mx.stream(stream):
                ids = mx.array([consumed], dtype=input_ids.dtype)
                text_positions = mx.broadcast_to(
                    mx.arange(first_position, first_position + offset)[None, None], (3, 1, offset),
                )
                current_positions = mx.concatenate([positions[..., :-1], text_positions], axis=-1)
                current_tokens = mx.concatenate([prefix, ids], axis=-1)
            self._sync(cache, current_positions, current_tokens)

        def without_snapshots(*args: Any, state_steps: int | None = None, **options: Any) -> tuple[
            mx.array, mx.array, None,
        ]:
            output, state = delta.gated_delta_update(*args, **options)
            return output, state, None

        next_tail_check = 30
        pending: Future[list[int]] | None = None
        try:
            while offset < max_tokens:
                remaining = max_tokens - offset
                if offset >= next_tail_check and remaining >= 24:
                    tail = repeated_tail(consumed[1:] + [seed], remaining)
                    if tail:
                        next_tail_check = offset + 32
                        try:
                            with mx.stream(stream):
                                matched = matches_target_tail(
                                    language, cache, seed, tail, input_ids.dtype,
                                    prompt_history + consumed[-repetition_context_size:],
                                    repetition_penalty, repetition_context_size,
                                )
                        except (MemoryError, RuntimeError, ValueError):
                            # A speculative allocation may fail under load. Keep the live cache.
                            next_tail_check, matched = max_tokens, False
                        if matched:
                            # The entire remaining budget was checked on this image; the
                            # outer generator still handles EOS and the normal stop criteria.
                            for token in tail:
                                yield token, None
                            return
                # A previous answer is only a proposal. Re-evaluate each position against
                # this image's target cache before yielding anything, including EOS.
                if offset < len(hint):
                    proposal = list(hint[offset:min(offset + _BLOCK, max_tokens)])
                else:
                    if pending is None:
                        sync_cpu()
                        width = min(_FIRST_BLOCK if not consumed else _BLOCK, max_tokens - offset)
                        cancel = Event()
                        pending = self.executor.submit(
                            propose, first_position + offset, seed, width, cancel, consumed.copy(),
                        )
                    proposal = pending.result()
                width = len(proposal)
                inputs = [seed, *proposal[:-1]]
                if pending is not None:
                    cancel = Event()
                    pending = self.executor.submit(
                        propose, first_position + offset + width, proposal[-1],
                        min(_BLOCK, max_tokens - offset - width), cancel, consumed + inputs,
                    )
                with mx.stream(stream):
                    block = mx.array([inputs], dtype=input_ids.dtype)
                    # The original rollback can reconstruct just the accepted recurrent state.
                    # Most proposed tokens match, so avoid writing every intermediate state.
                    with_states = verifier.gated_delta_update_with_states
                    verifier.gated_delta_update_with_states = without_snapshots
                    try:
                        hidden, _, states = language.speculative_verify_hidden(block, cache)
                    finally:
                        verifier.gated_delta_update_with_states = with_states
                    logits = vision_logits(hidden)
                    if repetition_penalty:
                        history = prompt_history + consumed[-repetition_context_size:]
                        logits = penalize_repeated_tokens(
                            logits, history, inputs, repetition_penalty, repetition_context_size,
                        )
                    selected = greedy_tokens(logits)
                    target = cast(list[int], selected.flatten().tolist())
                accepted = next(
                    (i for i, (draft, gold) in enumerate(zip(proposal, target, strict=True))
                     if draft != gold), width,
                )
                keep = min(accepted + 1, width)
                if accepted < width:
                    cancel.set()
                    hint = ()
                consumed.extend(inputs[:keep])
                offset += keep
                seed = target[keep - 1]
                for token in target[:keep]:
                    yield token, None

                if offset == max_tokens:
                    break
                if keep < width:
                    with mx.stream(stream):
                        language.rollback_speculative_cache(cache, states, accepted, width)
                if accepted < width and pending is not None:
                    pending.result()
                    pending = None
        finally:
            cancel.set()
            if pending is not None:
                wait((pending,))
