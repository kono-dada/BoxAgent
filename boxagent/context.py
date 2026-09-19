"""可替换的本地桌面观察适配器，与语音、任务执行器和形象系统无关。"""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from .config import ROOT, LOG_DIR


def focused_window():
    """选择前台应用最上层的普通窗口；不截整屏，也不改变焦点。"""
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
                and bounds.get("Width", 0) > 80 and bounds.get("Height", 0) > 80):
            return {"id": int(window["kCGWindowNumber"]), "app": str(app.localizedName())}
    return None


class WindowSummary:
    def __init__(self, emit, interval=15, model=ROOT / "models/qwen3.5-0.8b-mlx", max_size=960):
        self.emit, self.interval, self.model = emit, interval, Path(model)
        self.max_size = max_size
        self.process = self.task = None

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def command(self, *args):
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
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self.emit("context.loading", context_status="正在加载本地模型…", context_text="")
            if not self.model.is_dir() or not (ROOT / ".venv/bin/python").exists():
                raise RuntimeError("缺少本地 MLX 环境或模型，请按 docs/qwen-mlx-probe.md 准备")
            with (LOG_DIR / "context-worker.log").open("a") as errors:
                self.process = await asyncio.create_subprocess_exec(
                    "uv", "run", "--no-project", "--python", str(ROOT / ".venv/bin/python"),
                    str(ROOT / "scripts/window_summary_worker.py"), str(self.model),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=errors,
                    env={**os.environ, "HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"})
                await self.reply(120)
                while True:
                    started = time.monotonic()
                    await self.observe()
                    # 慢推理不积压；只允许一个正在执行的观察。
                    await asyncio.sleep(max(.1, self.interval - (time.monotonic() - started)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with (LOG_DIR / "context-worker.log").open("a") as errors:
                errors.write(f"\n观察失败：{exc!r}\n")
            self.emit("context.error", context_enabled=False, context_text="", context_status=
                      "桌面观察已暂停，请检查屏幕录制权限及本地模型环境")
        finally:
            if self.process and self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 2)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            self.process = None

    async def observe(self):
        from PIL import Image
        window = focused_window()
        if not window:
            self.emit("context.waiting", context_text="", context_status="等待可读取的前台窗口")
            return
        self.emit("context.reading", context_status="正在观察前台窗口…")
        captured_at = time.time()
        # 临时图片随本轮结束删除，模型和图片均不上传云端。
        with tempfile.TemporaryDirectory(prefix="boxagent-context-") as directory:
            path = Path(directory) / "window.png"
            await self.command("/usr/sbin/screencapture", "-x", "-o", "-l", str(window["id"]), str(path))
            with Image.open(path) as source:
                image = source.convert("RGB")
                image.thumbnail((self.max_size, self.max_size))
                image.save(path)
            self.process.stdin.write((json.dumps({"image": str(path)}) + "\n").encode())
            await self.process.stdin.drain()
            result = await self.reply(60)
        # 推理期间切换窗口时丢弃过时结果，避免把旧内容当作当前上下文。
        if focused_window() != window:
            self.emit("context.stale", context_text="", context_status="窗口已切换，等待下一次观察")
            return
        text = result.get("summary", "").strip()
        if not text:
            raise RuntimeError("模型返回空摘要")
        self.emit("context.updated", context_status="", context_text=text,
                  context_app=window["app"], context_at=captured_at)
