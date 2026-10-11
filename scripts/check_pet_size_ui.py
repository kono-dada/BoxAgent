"""验证真实菜单、原生窗口和 WebKit 的同步缩放；不使用语音或云端服务。"""

import asyncio
import base64
import json
import math
import os
import subprocess
import tempfile
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch
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
        check(desktop.pet_viewport == restored_size, "重启时渲染视口直接使用保存的大小")
        check(desktop.petViewportFrame()[0] == saved_origin,
              "重启保留透明视口部分在屏幕外的已保存位置")
        check(desktop.pet_size_slider.doubleValue() == 237
              and desktop.pet_size_label.stringValue() == "237%", "重启后滑块和百分比恢复")
        wait_ready(verify_mouse)

    @guarded
    def verify_mouse():
        appearance = desktop.appearance
        point = AK.NSMakePoint(500, 400)
        origin = desktop.petViewportFrame()[0]
        bubble_anchor = desktop.pet_bubble_frame
        with patch.object(AK, "NSEvent", SimpleNamespace(mouseLocation=lambda: point)):
            with patch.object(appearance, "interact", wraps=appearance.interact) as events:
                desktop.drag.mouseDown_(None)
                check(not events.called and not appearance.dragging, "仅按住鼠标不会进入拖拽状态")
                point = AK.NSMakePoint(502, 401)
                desktop.drag.mouseDragged_(None)
                check(not events.called, "阈值内的手抖不会触发拖拽")
                desktop.drag.mouseUp_(None)
                check([call.args[0] for call in events.call_args_list] == ["tap"],
                      "单击只发送 tap，没有 drag-start 或 drag-end")
                desktop.toggleBubble_(None)
                events.reset_mock()
                point = AK.NSMakePoint(500, 400)
                desktop.drag.mouseDown_(None)
                point = AK.NSMakePoint(520, 400)
                desktop.drag.mouseDragged_(None)
                check(appearance.dragging, "超过阈值后才真正进入拖拽状态")
                check(desktop.pet_bubble_frame[0] ==
                      (bubble_anchor[0][0] + 20, bubble_anchor[0][1]),
                      "实际拖动桌宠时，聊天框的固定基准跟随移动")
                point = AK.NSMakePoint(500, 400)
                desktop.drag.mouseDragged_(None)
                check(desktop.petViewportFrame()[0] == origin, "裁剪后的窗口拖回原点，角色位置不漂移")
                desktop.drag.mouseUp_(None)
                check([call.args[0] for call in events.call_args_list] ==
                      ["drag-start", "drag-move", "drag-end"], "一次拖动只发送一次开始和结束")
        AppHelper.callLater(0.7, measure_window_cost)

    @guarded
    def measure_window_cost():
        @guarded
        def started_sample(value, error):
            if error:
                raise RuntimeError(error)
            before = json.loads(value)

            @guarded
            def finished_sample(value, error):
                if error:
                    raise RuntimeError(error)
                after = json.loads(value)
                frames = after["frames"] - before["frames"]
                first, last = before["windowSizing"], after["windowSizing"]
                samples = last["checks"] - first["checks"]
                report["window_cost"] = {"seconds": 2, "frames": frames, "samples": samples,
                                         "window_updates": last["reports"] - first["reports"],
                                         "scan_milliseconds": last["milliseconds"] - first["milliseconds"]}
                check(frames >= 20 and samples < frames * .7,
                      "连续渲染期间边界采样次数明显少于动画帧数")
                finish()

            AppHelper.callLater(2, lambda: desktop.appearance.web.evaluateJavaScript_completionHandler_(
                "JSON.stringify(window.pet.debug())", finished_sample))
        desktop.appearance.web.evaluateJavaScript_completionHandler_(
            "window.pet.resume(); JSON.stringify(window.pet.debug())", started_sample)

    @guarded
    def switched():
        nonlocal restored_size, saved_origin
        restored_size = desktop.pet_viewport
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
        # 重启入口使用主屏；验收保存位置也使用同一屏，避免其他窗口改变当前屏。
        screen = AK.NSScreen.mainScreen().visibleFrame()
        saved_origin = (screen.origin.x - 40, screen.origin.y + 40)
        desktop.pet.setFrameOrigin_(saved_origin)
        desktop.savePosition()
        restart()

    scales = iter((0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 2.37))
    restored_size = None
    saved_origin = None
    bubble_origin = None
    cases = iter(("idle", "speaking", "drag-left", "drag-right", "release", "tap", "success"))

    @guarded
    def next_pose():
        nonlocal bubble_origin
        name = next(cases, None)
        if name is None:
            desktop.toggleBubble_(None)
            item = AK.NSMenuItem.alloc().init()
            item.setRepresentedObject_(str(settings.default_pet))
            desktop.appearance.web.evaluateJavaScript_completionHandler_("window.pet.resume()", None)
            desktop.useLocalVrm_(item)
            wait_ready(switched)
            return
        if bubble_origin is None:
            desktop.showBubble()
            bubble_origin = tuple(desktop.bubble.frame().origin)

        @guarded
        def verify_pose(value, error):
            if error:
                raise RuntimeError(error)
            result = json.loads(value)
            path = output / f"pose-{name}.png"
            path.write_bytes(base64.b64decode(result["image"].split(",", 1)[1]))
            check(desktop.pet_crop[1][0] < desktop.pet_viewport[0] * .75,
                  f"{name}：真实 WebKit 消息自动更新窗口")
            desktop.positionBubble()
            check(tuple(desktop.bubble.frame().origin) == bubble_origin,
                  f"{name}：角色动作和窗口裁剪不会移动聊天框")
            with Image.open(path) as picture:
                (x, y), (w, h) = desktop.pet_crop
                vw, vh = desktop.pet_viewport
                ratio = picture.width / vw
                rect = (round(x * ratio), round((vh - y - h) * ratio),
                        round((x + w) * ratio), round((vh - y) * ratio))
                alpha = picture.getchannel("A").getbbox()
                check(rect[0] <= alpha[0] and rect[1] <= alpha[1]
                      and rect[2] >= alpha[2] and rect[3] >= alpha[3],
                      f"{name}：动作变化后窗口仍完整包含角色")
                picture.crop(rect).save(output / f"pose-{name}-fitted.png")

            @guarded
            def capture_native():
                if name == "idle":
                    native = output / "native-fitted.png"
                    result = subprocess.run(["/usr/sbin/screencapture", "-x", "-o", "-l",
                                             str(desktop.pet.windowNumber()), str(native)],
                                            capture_output=True, timeout=10)
                    check(result.returncode == 0 and native.is_file(), "保存实际原生窗口截图")
                    with Image.open(native) as picture:
                        check(picture.getchannel("A").getbbox() is not None,
                              "原生窗口裁剪后仍实际显示角色")
                next_pose()
            AppHelper.callLater(0.15, capture_native)

        def received(value, error):
            # JavaScript 求值回调可能早于边界消息；等待真实桥接消息到达。
            AppHelper.callLater(0.1, lambda: verify_pose(value, error))

        script = (f"JSON.stringify({{image:window.pet.preview({json.dumps(name)}, .8).image,"
                  "bounds:window.pet.bounds()})")
        desktop.appearance.web.evaluateJavaScript_completionHandler_(script, received)

    @guarded
    def next_scale():
        scale = next(scales, None)
        if scale is None:
            next_pose()
            return
        slider = desktop.pet_size_slider
        check(slider.isContinuous() and slider.minValue() == 50 and slider.maxValue() == 600,
              "滑块连续调整，范围 50%～600%")
        slider.setDoubleValue_(scale * 100)
        slider.sendAction_to_(slider.action(), slider.target())
        screen = desktop.pet.screen().visibleFrame()
        fitted_scale = min(scale, screen.size.width / 252, screen.size.height / 182)
        expected = (math.ceil(252 * fitted_scale), math.ceil(182 * fitted_scale))
        viewport_origin = desktop.petViewportFrame()[0]
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
                message = desktop.appearance.render_bounds
                report["bridge"] = {"message": str(message),
                                    "viewport_types": [str(type(v)) for v in message.get("viewport", ())],
                                    "bounds_types": {k: str(type(v)) for k, v in message.get("bounds", {}).items()}}
                check(desktop.pet_crop[1][0] < expected[0] * .75,
                      f"{round(scale * 100)}%：真实 WebKit 消息自动收紧窗口")
                # 消息与截图来自同一帧，按实际透明像素独立检查裁剪完整性。
                check(desktop.petViewportFrame()[0] == viewport_origin,
                      f"{round(scale * 100)}%：收紧窗口不移动角色")
                (x, y), (w, h) = desktop.pet_crop
                ratio = picture.width / expected[0]
                rect = (round(x * ratio), round((expected[1] - y - h) * ratio),
                        round((x + w) * ratio), round((expected[1] - y) * ratio))
                alpha = picture.getchannel("A").getbbox()
                check(rect[0] <= alpha[0] and rect[1] <= alpha[1]
                      and rect[2] >= alpha[2] and rect[3] >= alpha[3],
                      f"{round(scale * 100)}%：窗口完整包含角色的所有非透明像素")
                check(w < expected[0] * .75 and h <= expected[1],
                      f"{round(scale * 100)}%：窗口移除两侧的大量透明空白")
                check(tuple(desktop.appearance.view.frame().size) == tuple(desktop.pet.frame().size)
                      and tuple(desktop.appearance.web.frame().size) == expected,
                      f"{round(scale * 100)}%：原生窗口收紧且保留模型原来的渲染比例")
                picture.crop(rect).save(output / f"pet-{round(scale * 100)}-fitted.png")
                report["sizes"].append({"scale": scale, "pixels": list(picture.size),
                                        "window": [w, h]})
            next_scale()

        AppHelper.callLater(0.5, lambda: desktop.appearance.web.evaluateJavaScript_completionHandler_(
            "JSON.stringify({width:innerWidth,height:innerHeight,image:window.pet.screenshot(),"
            "bounds:window.pet.bounds()})", captured))

    desktop = create()
    app.setDelegate_(desktop)
    AppHelper.callLater(0.1, lambda: wait_ready(next_scale))
    AppHelper.runEventLoop()
