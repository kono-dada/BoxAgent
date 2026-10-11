"""验证真实菜单、原生窗口和 WebKit 的同步缩放；不使用语音或云端服务。"""

import asyncio
import base64
import json
import math
import os
import tempfile
import time
import traceback
from pathlib import Path

import AppKit as AK
from PIL import Image
from PyObjCTools import AppHelper

import boxagent.interfaces.macos.app as host
from boxagent.application.assistant import BoxAgentApplication
from boxagent.bootstrap.settings import load_settings
from boxagent.interfaces.macos.inprocess_bridge import BackendBridge
from boxagent.interfaces.macos.pets.catalog import PetCatalog
from boxagent.interfaces.macos.pets.vrm import VrmAppearance


class PreviewExecutor:
    async def run(self, goal, progress, approve):
        await asyncio.Event().wait()

    async def cancel(self):
        pass


class PreviewHotkey:
    registered = True

    def __init__(self, _callback):
        pass

    def close(self):
        pass


def main():
    settings = load_settings()
    output = settings.root / ".runtime/pet-size-check"
    output.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(dir=output)
    data = Path(temporary.name)
    soul = data / "SOUL.md"
    soul.write_text("名字：缩放验收\n")
    catalog = PetCatalog(data / "pets", default_pet=settings.default_pet,
                         bundled_root=settings.root / "assets/vrm/models")
    host.Hotkey = PreviewHotkey
    app = AK.NSApplication.sharedApplication()
    desktop = None
    report = {"checks": [], "sizes": []}
    started = time.monotonic()

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        report["checks"].append(name)

    def create():
        backend = BackendBridge(lambda publish: BoxAgentApplication(
            publish, lambda _id: PreviewExecutor(), None), log_dir=data / "logs")
        return host.Desktop.alloc().init().configure(
            backend, VrmAppearance(settings.default_pet), catalog, data_dir=data,
            appearance_factory=VrmAppearance, soul_file=soul, editable_soul_file=soul)

    def finish(error=None):
        if error:
            report["error"] = str(error)
        if desktop:
            desktop.applicationShouldTerminate_(app)
            for name in ("pet", "bubble", "context_bubble"):
                getattr(desktop, name).orderOut_(None)
            AK.NSStatusBar.systemStatusBar().removeStatusItem_(desktop.status_item)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False), flush=True)
        temporary.cleanup()
        # NSApplication 的 terminate 会把失败也报为零，显式保留验收退出码。
        os._exit(1 if error else 0)

    def guarded(callback):
        def wrapped(*args):
            try:
                callback(*args)
            except Exception as error:
                traceback.print_exc()
                finish(error)
        return wrapped

    def wait_ready(following):
        @guarded
        def poll():
            appearance = desktop.appearance
            if appearance.error:
                raise RuntimeError(appearance.error)
            if time.monotonic() - started > 90:
                raise TimeoutError("缩放验收超时")
            if getattr(desktop, "pending_appearance", None) or not appearance.ready:
                AppHelper.callLater(0.1, poll)
                return
            following()
        poll()

    @guarded
    def restart():
        nonlocal desktop
        desktop.applicationShouldTerminate_(app)
        for name in ("pet", "bubble", "context_bubble"):
            getattr(desktop, name).orderOut_(None)
        AK.NSStatusBar.systemStatusBar().removeStatusItem_(desktop.status_item)
        desktop = create()
        app.setDelegate_(desktop)
        desktop.applicationDidFinishLaunching_(None)
        check(desktop.pet_scale == 2.37, "重新创建宿主从文件恢复连续比例 237%")
        check(tuple(desktop.pet.frame().size) == restored_size, "重启时原生窗口直接使用保存的大小")
        check(desktop.pet_size_slider.doubleValue() == 237
              and desktop.pet_size_label.stringValue() == "237%", "重启后滑块和百分比恢复")
        wait_ready(lambda: finish())

    @guarded
    def switched():
        nonlocal restored_size
        restored_size = tuple(desktop.pet.frame().size)
        check(desktop.pet_scale == 2.37,
              "运行中切换 VRM 保留缩放比例和窗口大小")
        check(tuple(desktop.appearance.web.frame().size) == restored_size,
              "新形象的 WebKit 同步使用缩放大小")
        check(not desktop.bubble.isVisible(), "缩放与切换形象不会打开对话")
        controls = desktop.menu.itemWithTitle_("桌宠大小").submenu().itemAtIndex_(0).view()
        reset = next(view for view in controls.subviews() if isinstance(view, AK.NSButton))
        reset.performClick_(None)
        check(desktop.pet_scale == 1 and desktop.pet_size_slider.doubleValue() == 100
              and tuple(desktop.pet.frame().size) == (252, 182), "恢复默认按钮重置滑块与角色大小")
        desktop.pet_size_slider.setDoubleValue_(237)
        desktop.pet_size_slider.sendAction_to_(desktop.pet_size_slider.action(), desktop)
        restart()

    scales = iter((0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 2.37))
    restored_size = None

    @guarded
    def next_scale():
        scale = next(scales, None)
        if scale is None:
            item = AK.NSMenuItem.alloc().init()
            item.setRepresentedObject_(str(settings.default_pet))
            desktop.useLocalVrm_(item)
            wait_ready(switched)
            return
        slider = desktop.pet_size_slider
        check(slider.isContinuous() and slider.minValue() == 50 and slider.maxValue() == 600,
              "滑块连续调整，范围 50%～600%")
        slider.setDoubleValue_(scale * 100)
        slider.sendAction_to_(slider.action(), slider.target())
        screen = desktop.pet.screen().visibleFrame()
        fitted_scale = min(scale, screen.size.width / 252, screen.size.height / 182)
        expected = (math.ceil(252 * fitted_scale), math.ceil(182 * fitted_scale))
        report["last_frames"] = {"scale": scale, "window": str(desktop.pet.frame()),
                                 "container": str(desktop.appearance.view.frame()),
                                 "web": str(desktop.appearance.web.frame())}
        check(tuple(desktop.pet.frame().size) == expected
              and tuple(desktop.appearance.web.frame().size) == expected,
              f"{round(scale * 100)}%：滑块同步缩放窗口与 WebKit")
        check(desktop.pet_size_label.stringValue() == f"{round(scale * 100)}%",
              f"{round(scale * 100)}%：百分比实时更新")

        @guarded
        def captured(value, error):
            if error:
                raise RuntimeError(error)
            result = json.loads(value)
            check((result["width"], result["height"]) == expected,
                  f"{round(scale * 100)}%：渲染网页收到实际视口大小")
            path = output / f"pet-{round(scale * 100)}.png"
            path.write_bytes(base64.b64decode(result["image"].split(",", 1)[1]))
            with Image.open(path) as picture:
                check(picture.getchannel("A").getextrema() == (0, 255),
                      f"{round(scale * 100)}%：实际画面包含透明背景和角色")
                report["sizes"].append({"scale": scale, "pixels": list(picture.size)})
            next_scale()

        AppHelper.callLater(0.5, lambda: desktop.appearance.web.evaluateJavaScript_completionHandler_(
            "JSON.stringify({width:innerWidth,height:innerHeight,image:window.pet.screenshot()})", captured))

    desktop = create()
    app.setDelegate_(desktop)
    AppHelper.callLater(0.1, lambda: wait_ready(next_scale))
    AppHelper.runEventLoop()
