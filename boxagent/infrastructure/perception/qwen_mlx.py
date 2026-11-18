"""可替换的本地桌面观察适配器，与语音、任务执行器和形象系统无关。"""

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from boxagent.domain.memory.visual import VisualFrame
from boxagent.infrastructure.perception.windows import WindowInfo

logger = logging.getLogger(__name__)

# 连续这么多次截图失败才停止观察；单次失败多为窗口正在切换、缩放或关闭。
MAX_CAPTURE_FAILURES = 3
_SENTENCE = re.compile(r"[^。！？!?\n]+[。！？!?]?")


def clean_summary(text):
    """去掉复读的句子，并丢弃被长度上限截断的半句。

    小模型偶尔把同一句话重复到 max_tokens；这里按句去重（忽略首尾空白），
    保留第一次出现的顺序。若最后一句没有结束标点且前面已有完整句子，
    视为截断残句并丢弃。
    """
    seen, kept = set(), []
    for match in _SENTENCE.finditer(text.strip()):
        sentence = match.group(0).strip()
        key = sentence.rstrip("。！？!?")
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(sentence)
    if len(kept) > 1 and not kept[-1].endswith(("。", "！", "？", "!", "?")):
        kept.pop()
    return "".join(kept)


def focused_window() -> WindowInfo | None:
    """选择前台应用最上层的普通窗口；不截整屏，也不改变焦点。"""
    if sys.platform == "win32":
        from boxagent.infrastructure.perception.windows import focused_window as windows_focused_window
        return windows_focused_window()
    if sys.platform != "darwin":
        raise RuntimeError(f"不支持的桌面操作系统：{sys.platform}")
    import AppKit as AK
    import Quartz as Q
    app = AK.NSWorkspace.sharedWorkspace().frontmostApplication()
    if not app or app.processIdentifier() == os.getpid():
        return None
    windows = Q.CGWindowListCopyWindowInfo(
        Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements,
        Q.kCGNullWindowID)
    for window in windows:
        bounds = window.get("kCGWindowBounds", {})
        if (window.get("kCGWindowOwnerPID") == app.processIdentifier()
                and window.get("kCGWindowLayer") == 0
                and window.get("kCGWindowAlpha", 1) > 0
                and bounds.get("Width", 0) > 80 and bounds.get("Height", 0) > 80):
            return {"id": int(window["kCGWindowNumber"]), "app": str(app.localizedName())}
    return None


class WindowSummary:
    def __init__(self, emit, interval=15, model=None, max_size=960, *, workspace=None,
                 python=None, worker_script=None, log_path=None):
        self.workspace = Path(workspace or Path.cwd())
        self.emit, self.interval = emit, interval
        self.model = Path(model or self.workspace / (".runtime/models/qwen3.5-0.8b" if os.name == "nt" else ".runtime/models/qwen3.5-0.8b-mlx"))
        self.python = Path(python or self.workspace / (".runtime/vision-venv/Scripts/python.exe" if os.name == "nt" else ".runtime/vision-venv/bin/python"))
        self.worker_script = Path(worker_script or self.workspace / "scripts/window_summary_worker.py")
        self.log_path = Path(log_path or self.workspace / ".runtime/pet/context-worker.log")
        self.max_size = max_size
        self.process = self.task = None
        self.capture_failures = 0
        self.latest_frame: VisualFrame | None = None
        self.inference_lock = asyncio.Lock()
        self.model_errors = None

    async def stop(self):
        self.latest_frame = None
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        await self._stop_model()

    def current_frame(self, *, requested_at: float) -> VisualFrame | None:
        frame = self.latest_frame
        if frame is None or not 0 <= time.time() - frame.captured_at <= 45 \
                or not 0 <= requested_at - frame.captured_at <= 45:
            return None
        window = focused_window()
        return frame if window and window["app"] == frame.app and window["id"] == frame.window_id else None

    async def _stop_model(self) -> None:
        process, self.process = self.process, None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self.model_errors:
            self.model_errors.close()
            self.model_errors = None

    async def _ensure_model(self) -> None:
        if self.process and self.process.returncode is None:
            return
        if not self.model.is_dir() or not self.python.exists():
            raise RuntimeError("缺少本地视觉环境或模型")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_errors = self.log_path.open("a")
        self.process = await asyncio.create_subprocess_exec(
            "uv", "run", "--no-project", "--python", str(self.python),
            str(self.worker_script), str(self.model),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=self.model_errors,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"})
        await self.reply(120)

    async def _infer(self, path: Path, question: str = "") -> dict:
        async with self.inference_lock:
            try:
                await self._ensure_model()
                self.process.stdin.write((json.dumps({"image": str(path), "question": question}) + "\n").encode())
                await self.process.stdin.drain()
                return await self.reply(60)
            except (asyncio.CancelledError, TimeoutError, RuntimeError, OSError):
                # An unread reply must never become the next request's answer.
                await self._stop_model()
                raise

    async def describe(self, image: bytes, question: str) -> str:
        """Reread saved bytes locally; no new desktop capture or image upload."""
        with tempfile.TemporaryDirectory(prefix="boxagent-memory-look-") as directory:
            path = Path(directory) / "source.png"
            path.write_bytes(image)
            result = await self._infer(path, question[:500])
        text = clean_summary(str(result.get("summary", "")))
        if not text:
            raise RuntimeError("原图回看没有返回可见内容")
        return text

    async def capture(self, window_id: int, path: Path) -> None:
        if sys.platform == "win32":
            from boxagent.infrastructure.perception.windows import capture_window
            await asyncio.to_thread(capture_window, window_id, path)
        elif sys.platform == "darwin":
            await self.command("/usr/sbin/screencapture", "-x", "-o", "-l",
                               str(window_id), str(path))
        else:
            raise RuntimeError(f"不支持的桌面操作系统：{sys.platform}")

    async def command(self, *args: str) -> None:
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                       stderr=asyncio.subprocess.PIPE)
        try:
            _, error = await asyncio.wait_for(process.communicate(), 8)
            if process.returncode:
                raise RuntimeError(error.decode(errors="replace"))
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def reply(self, timeout):
        line = await asyncio.wait_for(self.process.stdout.readline(), timeout)
        if not line:
            raise RuntimeError("本地模型进程已退出")
        result = json.loads(line)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    async def run(self):
        self.task = asyncio.current_task()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.emit("context.loading", context_status="正在加载本地模型…", context_text="")
            while True:
                started = time.monotonic()
                await self.observe()
                await asyncio.sleep(max(.1, self.interval - (time.monotonic() - started)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("desktop observation stopped", exc_info=True)
            with self.log_path.open("a") as errors:
                errors.write(f"\n观察失败：{exc!r}\n")
            self.emit("context.error", context_enabled=False, context_text="", context_status=
                      "桌面观察已暂停，请检查屏幕录制权限及本地模型环境")
        finally:
            self.latest_frame = None
            await self._stop_model()

    async def observe(self):
        from PIL import Image
        window = focused_window()
        if not window:
            self.latest_frame = None
            self.emit("context.waiting", context_text="", context_status="等待可读取的前台窗口")
            return
        self.emit("context.reading", context_status="正在观察前台窗口…")
        captured_at = time.time()
        # 临时图片随本轮结束删除，模型和图片均不上传云端。
        with tempfile.TemporaryDirectory(prefix="boxagent-context-") as directory:
            path = Path(directory) / "window.png"
            try:
                await self.capture(window["id"], path)
            except (RuntimeError, TimeoutError, OSError) as exc:
                # 窗口在列举与截图之间关闭、缩放或被系统暂时拒绝时会失败；
                # 跳过这一轮，连续失败才视为权限或环境问题。
                self.capture_failures += 1
                logger.warning("window capture failed (%d/%d) app=%s: %s",
                               self.capture_failures, MAX_CAPTURE_FAILURES,
                               window["app"], str(exc).strip() or type(exc).__name__)
                if self.capture_failures >= MAX_CAPTURE_FAILURES:
                    raise
                self.emit("context.waiting", context_text="",
                          context_status="前台窗口暂时无法截图，稍后重试")
                return
            self.capture_failures = 0
            with Image.open(path) as source:
                image = source.convert("RGB")
                image.thumbnail((self.max_size, self.max_size))
                image.save(path)
            image_bytes = path.read_bytes()
            result = await self._infer(path)
        # 推理期间切换窗口时丢弃过时结果，避免把旧内容当作当前上下文。
        if focused_window() != window:
            self.latest_frame = None
            self.emit("context.stale", context_text="", context_status="窗口已切换，等待下一次观察")
            return
        raw = result.get("summary", "").strip()
        text = clean_summary(raw)
        if len(text) < len(raw):
            logger.info("summary cleaned", extra={"data": {
                "raw_chars": len(raw), "clean_chars": len(text)}})
        if not text:
            raise RuntimeError("模型返回空摘要")
        self.latest_frame = VisualFrame(image_bytes, text, captured_at, window["app"], window["id"])
        self.emit("context.updated", context_status="", context_text=text,
                  context_app=window["app"], context_window_id=window["id"],
                  context_at=captured_at)
