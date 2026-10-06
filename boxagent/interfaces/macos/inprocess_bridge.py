"""Bridge AppKit's main thread to the asyncio application lifecycle."""

import asyncio
import concurrent.futures
import json
import os
import queue
import threading
import time
import traceback

from boxagent.core.errors import redact


class BackendBridge:
    def __init__(self, application_factory, *, log_dir):
        self.log_dir = log_dir
        self.events = queue.SimpleQueue()
        self.loop = asyncio.new_event_loop()
        self.application = application_factory(self.publish)
        self.publish({"type": "app.started", "occurred_at": time.time(), "pid": os.getpid()})
        self.thread = threading.Thread(target=self.run, name="BoxAgent-backend", daemon=True)
        self.thread.start()
        self.startup = self.submit(self.application.start())

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.set_exception_handler(lambda _loop, context: self.publish({
            "type": "backend.error", "occurred_at": time.time(),
            "error": "后台出现异常，请重启后重试",
            "detail": repr(context.get("exception") or context.get("message"))}))
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.close()

    def publish(self, event):
        self.events.put(event)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with (self.log_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(redact(event), ensure_ascii=False) + "\n")

    def submit(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)

        def finished(done):
            try:
                done.result()
            except concurrent.futures.CancelledError:
                pass
            except Exception as exc:
                self.publish({"type": "backend.error", "occurred_at": time.time(),
                              "error": "请求未能处理，请稍后重试", "detail": repr(exc),
                              "traceback": traceback.format_exc()})
        future.add_done_callback(finished)
        return future

    def close(self):
        try:
            asyncio.run_coroutine_threadsafe(
                self.application.close(), self.loop).result(timeout=8)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
        self.publish({"type": "app.stopped", "occurred_at": time.time(), "pid": os.getpid()})
