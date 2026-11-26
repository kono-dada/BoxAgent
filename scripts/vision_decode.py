"""Optional Metal MTP proposals for the original Qwen vision and language model."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from importlib import import_module
from pathlib import Path
from typing import Any, cast

import mlx.core as mx


def draft_token_ids(tokenizer: Any, vocab_size: int) -> list[int]:
    # Only the predictor uses this subset. The target still scores every vocabulary row.
    tokens = set(range(min(4096, vocab_size)))
    fields = ("scene", "observation", "observed_frames", "praise", "detail", "frames", "Frame")
    for key in ("fighting", "settled", "unclear", *fields):
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


class MetalDraft:
    def __init__(self, directory: Path) -> None:
        self.draft_directory: Path | None = directory
        self.drafter: Any | None = None
        self.verify_logits: Callable[[mx.array], mx.array] | None = None
        self.verify_tokens: Callable[[mx.array], mx.array] | None = None

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

            head, full_logits = qwen_verification_head(model.language_model)
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

    def generate(
        self, generate: Callable[..., Any], model: Any, processor: Any, prompt: str,
        image: str, options: dict[str, Any],
    ) -> Any | None:
        from wuwa_cpu_draft import supports_greedy_speculation

        if not supports_greedy_speculation(options):
            return None
        self._load_drafter(model, processor)
        if self.drafter is None or self.verify_tokens is None:
            return None
        language = model.language_model
        original = language.speculative_argmax_from_hidden
        language.speculative_argmax_from_hidden = self.verify_tokens
        try:
            with self._metal_repetition(
                options.get("repetition_penalty"), options.get("repetition_context_size", 64),
            ):
                return generate(model, processor, prompt, image=[image], verbose=False,
                                **options, draft_model=self.drafter, draft_kind="mtp",
                                draft_block_size=3)
        except (AttributeError, RuntimeError, ValueError) as exc:
            self.drafter = None
            print(f"Vision MTP failed; using standard decode: {exc}", file=sys.stderr)
            return None
        finally:
            language.speculative_argmax_from_hidden = original
