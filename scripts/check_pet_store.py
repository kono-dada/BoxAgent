"""真实商店与原生换装验收；任务和语音使用替身，不录音、不操作其他应用。"""

import asyncio
import json
import time
import traceback

import AppKit as AK
from Foundation import NSDate, NSRunLoop
from PyObjCTools import AppHelper

import boxagent.desktop as host
from boxagent.appearance.codex_pets import CodexPetsAppearance
from boxagent.config import DEFAULT_PET, ROOT
from boxagent.domain import Snapshot
from boxagent.pets.catalog import PetCatalog
from boxagent.runtime import Runtime


class PreviewExecutor:
    async def run(self, goal, progress, approve):
        await asyncio.Event().wait()

    async def cancel(self):
        pass


class PreviewVoice:
    def __init__(self, handle_tool, emit):
        self.emit = emit
        self.done = asyncio.Event()

    async def run(self):
        self.emit("voice.ready", voice="ready")
        await self.done.wait()

    async def stop(self):
        self.done.set()


class PreviewHotkey:
    registered = True

    def __init__(self, _callback):
        pass

    def close(self):
        pass


def main():
    output = ROOT / ".runtime/pet-store-check"
    output.mkdir(parents=True, exist_ok=True)
    host.DATA = host.LOG_DIR = output
    host.Hotkey = PreviewHotkey
    catalog = PetCatalog(output / "pets")
    backend = host.Backend(lambda publish: Runtime(publish, lambda _id: PreviewExecutor(), PreviewVoice))
    app = AK.NSApplication.sharedApplication()
    app.setActivationPolicy_(AK.NSApplicationActivationPolicyAccessory)
    desktop = host.Desktop.alloc().init().configure(backend, CodexPetsAppearance(DEFAULT_PET), catalog)
    app.setDelegate_(desktop)
    result = {"checks": [], "screenshots": [], "network": "真实公开商店", "voice_and_executor": "替身"}
    finished = False

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        result["checks"].append(name)

    def finish():
        nonlocal finished
        if finished:
            return
        finished = True
        (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        desktop.applicationShouldTerminate_(app)
        AppHelper.stopEventLoop()

    def guarded(callback):
        try:
            callback()
        except Exception:
            result["error"] = traceback.format_exc()
            finish()

    def wait_for(condition, callback, timeout=90):
        deadline = time.monotonic() + timeout
        def poll():
            if finished:
                return
            if condition():
                callback()
            elif time.monotonic() > deadline:
                raise TimeoutError(f"等待 {callback.__name__} 超时：{desktop.pet_store.status.stringValue()}")
            else:
                AppHelper.callLater(.1, lambda: guarded(poll))
        AppHelper.callLater(.1, lambda: guarded(poll))

    def capture(name, view):
        view.displayIfNeeded()
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.15))
        bitmap = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
        view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), bitmap)
        bitmap.representationUsingType_properties_(AK.NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(str(output / f"{name}.png"), True)
        result["screenshots"].append(name)

    def start():
        desktop.showPetStore_(None)
        wait_for(lambda: bool(desktop.pet_store.items), first_page)

    def first_page():
        store = desktop.pet_store
        check(len(store.items) == 12, "真实目录首页加载 12 项")
        capture("store", store.window.contentView())
        store.next_(None)
        wait_for(lambda: store.page == 2 and store.next.isEnabled(), second_page)

    def second_page():
        store = desktop.pet_store
        check(store.page == 2, "商店分页可用")
        store.search.setStringValue_("guga")
        store.search_(None)
        wait_for(lambda: any(p["id"] == "guga" for p in store.items), choose_v1)

    def choose_v1():
        store = desktop.pet_store
        index = next(i for i, pet in enumerate(store.items) if pet["id"] == "guga")
        store.choose_buttons[index].performClick_(None)
        check(not store.search.isEnabled() and not store.filter.isEnabled(), "切换期间禁用搜索和筛选，保留进度提示")
        wait_for(lambda: not store.selecting, switched_v1)

    def switched_v1():
        check(desktop.appearance.directory.name == "guga", "点击商店按钮下载并切换真实 V1")
        check(desktop.appearance.version == 1, "V1 按九行图集播放")
        desktop.appearance.present(Snapshot(), time.monotonic())
        capture("v1", desktop.drag)
        check(PetCatalog(catalog.root).current_directory().name == "guga", "新实例离线恢复已选 V1")
        backend.submit(backend.runtime.submit_text("换装期间保持这个测试任务运行"))
        backend.submit(backend.runtime.toggle_voice())
        store = desktop.pet_store
        store.search.setStringValue_("gugahd")
        store.search_(None)
        wait_for(lambda: any(p["id"] == "gugahd" for p in store.items)
                 and backend.runtime.state.voice == "ready" and backend.runtime.state.task == "running", choose_v2)

    def choose_v2():
        desktop.tick_(None)
        desktop.input.setStringValue_("换装后仍保留的草稿")
        result["task_id"] = backend.runtime.state.task_id
        result["position"] = [desktop.pet.frame().origin.x, desktop.pet.frame().origin.y]
        store = desktop.pet_store
        index = next(i for i, pet in enumerate(store.items) if pet["id"] == "gugahd")
        store.choose_buttons[index].performClick_(None)
        wait_for(lambda: not store.selecting, switched_v2)

    def switched_v2():
        check(desktop.appearance.directory.name == "gugahd", "点击商店按钮下载并切换真实 V2")
        check(desktop.appearance.version == 2, "V2 按十一行图集播放")
        check(backend.runtime.state.task_id == result["task_id"] and not backend.runtime.job.done(), "换装不替换或取消正在运行的任务")
        check(not backend.runtime.voice_task.done() and backend.runtime.state.voice == "ready", "换装保留语音会话生命周期")
        check(desktop.input.stringValue() == "换装后仍保留的草稿", "换装保留输入草稿")
        check(result["position"] == [desktop.pet.frame().origin.x, desktop.pet.frame().origin.y], "换装保留桌宠位置")
        desktop.appearance.present(Snapshot(), time.monotonic(), (100, 100))
        check(desktop.appearance.frame_key[0] >= 9, "V2 鼠标朝向使用额外两行")
        capture("v2", desktop.drag)
        store = desktop.pet_store
        capture("selected", store.window.contentView())
        app.setAppearance_(AK.NSAppearance.appearanceNamed_(AK.NSAppearanceNameDarkAqua))
        store.window.contentView().setNeedsDisplay_(True)
        capture("dark", store.window.contentView())
        app.setAppearance_(AK.NSAppearance.appearanceNamed_(AK.NSAppearanceNameAqua))
        def offline(_url, _limit):
            raise OSError("验收主动断网")
        catalog.request = offline
        store.filter.selectItemAtIndex_(1)
        store.search.setStringValue_("")
        store.search_(None)
        wait_for(lambda: store.total == 2 and len(store.items) == 2, offline_loaded)

    def offline_loaded():
        store = desktop.pet_store
        check(len(store.items) == 2, "已下载筛选断网可用")
        capture("offline", store.window.contentView())
        store.select("guga", "咕嘎")
        wait_for(lambda: not store.selecting, offline_switched)

    def offline_switched():
        check(desktop.appearance.directory.name == "guga", "断网成功切换缓存形象")
        desktop.pet_store.select("missing-pet", "不存在的形象")
        wait_for(lambda: not desktop.pet_store.selecting, failure)

    def failure():
        check(desktop.appearance.directory.name == "guga", "失败下载保留当前视图")
        check(catalog.current_directory().name == "guga", "失败下载保留持久化选择")
        check("仍在使用原来的形象" in desktop.pet_store.status.stringValue(), "失败提示说明保留原形象")
        desktop.pet_store.builtin_(None)
        wait_for(lambda: not desktop.pet_store.selecting, restored)

    def restored():
        check(desktop.appearance.directory == DEFAULT_PET, "可恢复内置小鸭")
        check(catalog.current_directory() == DEFAULT_PET, "恢复小鸭写入启动选择")
        store = desktop.pet_store
        store.catalog = PetCatalog(output / "empty-pets", lambda *_: (_ for _ in ()).throw(OSError("断网")))
        store.search_(None)
        wait_for(lambda: not store.loading and store.total == 0, empty)

    def empty():
        check("还没有下载形象" in desktop.pet_store.status.stringValue(), "首次未下载时提示去全部形象挑选")
        capture("empty", desktop.pet_store.window.contentView())
        finish()

    AppHelper.callLater(.8, lambda: guarded(start))
    AppHelper.runEventLoop()
    if result.get("error"):
        raise SystemExit(result["error"])


if __name__ == "__main__":
    main()
