"""Local Laya inference; no TypeSafe or OpenAI credentials are passed to Laya."""

import importlib
import threading
import time

from .local_device import local_model_device


class LayaBackend:
    def __init__(self, config):
        mlx = config.decision_backend == "laya-mlx"
        try:
            laya = importlib.import_module("laya_mlx" if mlx else "laya")
        except ModuleNotFoundError as exc:
            instruction = ("python -m pip install '.[mlx]' (Apple Silicon, macOS 14+)"
                           if mlx else "python -m pip install '.[laya]'")
            raise ImportError(f"{config.decision_backend} backend requires: {instruction}") from exc
        self.model = config.laya_model
        self.subfolder = config.laya_subfolder
        self.batch_size = config.laya_batch_size
        # MLX uses its own device policy; the PyTorch macOS CPU workaround does
        # not apply to this native runtime. Load before decision budgets start.
        options = {"subfolder": self.subfolder or None}
        if mlx:
            options.update(device=None if config.laya_device == "auto" else config.laya_device,
                           batch_size=self.batch_size)
        else:
            options["device"] = local_model_device(config.laya_device)
        self.agent = laya.load(self.model, **options)
        self.lock = threading.Lock()

    def predict(self, state, questions, timeout):
        deadline = time.monotonic() + timeout
        if not self.lock.acquire(timeout=max(0.0, timeout)):
            raise TimeoutError("laya_deadline")
        try:
            if self.agent is None:
                raise RuntimeError("laya_backend_closed")
            answers = {}
            usage = {"input_tokens": 0, "output_tokens": 0}
            items = list(questions.items())
            for offset in range(0, len(items), self.batch_size):
                if time.monotonic() >= deadline:
                    raise TimeoutError("laya_deadline")
                result = self.agent.predict(state, dict(items[offset:offset + self.batch_size]))
                # Local inference cannot be interrupted safely mid-forward-pass.
                # Reject late results and never launch another batch after expiry.
                if time.monotonic() >= deadline:
                    raise TimeoutError("laya_deadline")
                answers.update(result["answers"])
                for name in usage:
                    usage[name] += result.get("usage", {}).get(name, 0)
            return {
                "model": self.model + ("/" + self.subfolder if self.subfolder else ""),
                "answers": answers,
                "usage": usage,
            }
        finally:
            self.lock.release()

    def close(self):
        with self.lock:
            self.agent = None
