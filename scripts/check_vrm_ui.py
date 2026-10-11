"""原生宿主验收：真实渲染、状态切换、透明像素和换装释放。"""
import base64
import asyncio
import io
import json
import os
import shutil
import time
import traceback
from pathlib import Path

import AppKit as AK
from PIL import Image, ImageChops, ImageDraw, ImageFont
from PyObjCTools import AppHelper
import boxagent.interfaces.macos.app as host
from boxagent.application.assistant import BoxAgentApplication
from boxagent.bootstrap.settings import load_settings
from boxagent.agent.harness.persona import load_persona
from boxagent.core.states import Snapshot
from boxagent.interfaces.macos.inprocess_bridge import BackendBridge
from boxagent.interfaces.macos.pets.catalog import PetCatalog
from boxagent.interfaces.macos.pets.vrm import VrmAppearance

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("BOXAGENT_VRM_PET_DIR", str(ROOT / "assets/vrm/models/zome")))
CONTROLS_ONLY = os.environ.get("BOXAGENT_VRM_CONTROLS_ONLY") == "1"
OUTPUT = ROOT / (".runtime/vrm-control-check" if CONTROLS_ONLY else ".runtime/vrm-check") / SOURCE.name


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
    OUTPUT.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    local = OUTPUT / "pets" / SOURCE.name
    shutil.copytree(SOURCE, local, dirs_exist_ok=True)
    alternate = OUTPUT / "pets/local-vrm-preview"
    shutil.copytree(SOURCE, alternate, dirs_exist_ok=True)
    variant = json.loads((alternate / "pet.json").read_text())
    variant.update(displayName="VRM 换装验收", physicsSettings={"impactMultiplier": 0.3})
    (alternate / "pet.json").write_text(json.dumps(variant))
    catalog = PetCatalog(local.parent, default_pet=local, bundled_root=OUTPUT / "bundled")
    app = AK.NSApplication.sharedApplication()
    app.setActivationPolicy_(AK.NSApplicationActivationPolicyAccessory)
    host.Hotkey = PreviewHotkey
    backend = BackendBridge(lambda publish: BoxAgentApplication(
        publish, lambda _id: PreviewExecutor(), None), log_dir=OUTPUT)

    def factory(directory):
        return VrmAppearance(directory)

    appearance = factory(local)
    soul = OUTPUT / "SOUL.md"
    shutil.copyfile(settings.soul_file, soul)
    desktop = host.Desktop.alloc().init().configure(
        backend, appearance, catalog, data_dir=OUTPUT, appearance_factory=factory,
        persona=load_persona(soul), persona_loader=load_persona,
        soul_file=soul, editable_soul_file=soul)
    app.setDelegate_(desktop)
    result = {"checks": [], "voice_and_tasks": "本地替身",
              "scope": "状态与宿主接口，不做视觉一致性验收" if CONTROLS_ONLY else "完整渲染检查"}
    started = time.monotonic()
    finished = False

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        result["checks"].append(name)

    check(len(appearance.view.subviews()) == 1 and appearance.view.subviews()[0] == appearance.web,
          "3D 加载容器只包含 WebKit，没有 2D 占位视图")
    check(not appearance.ready and appearance.web.isHidden(), "3D 首帧就绪前保持透明")

    def finish(error=None):
        nonlocal finished
        if finished:
            return
        finished = True
        if error:
            result["error"] = str(error)
        try:
            desktop.applicationShouldTerminate_(app)
        except Exception as cleanup_error:
            # 初始化未完成时也必须输出本次失败报告，避免误读上一次成功结果。
            result["error"] = result.get("error") or str(cleanup_error)
            result["cleanup_error"] = str(cleanup_error)
            appearance.close()
            backend.close()
        for name in ("pet", "bubble"):
            window = getattr(desktop, name, None)
            if window is not None:
                window.orderOut_(None)
        result.pop("window_frame", None)
        (OUTPUT / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        AppHelper.stopEventLoop()

    def guarded(callback):
        def wrapped(*args):
            try:
                callback(*args)
            except Exception as error:
                traceback.print_exc()
                finish(error)
        return wrapped

    def capture(name, following):
        @guarded
        def received(value, error):
            if error:
                raise RuntimeError(error)
            data = base64.b64decode(value.split(",", 1)[1])
            (OUTPUT / f"{name}.png").write_bytes(data)
            alpha = Image.open(io.BytesIO(data)).getchannel("A")
            check(alpha.getextrema() == (0, 255), f"{name}：有透明背景和不透明角色")
            following()
        appearance.web.evaluateJavaScript_completionHandler_("window.pet.screenshot()", received)

    @guarded
    def switched():
        candidate = desktop.appearance
        pending = getattr(desktop, "pending_appearance", None)
        if pending is not None:
            if time.monotonic() - started > 90:
                raise AssertionError("换回本地形象超时")
            AppHelper.callLater(0.1, switched)
            return
        check(isinstance(candidate, VrmAppearance) and candidate.ready, "菜单入口可在两个 VRM 形象之间切换")
        check(catalog.current_directory() == alternate, "只在渲染成功后保存 VRM 选择")
        check(appearance.closed and appearance.server.http.fileno() == -1, "切换 VRM 释放旧渲染器与资源服务")
        check(desktop.input.stringValue() == "未发送的换装检查草稿", "换装保留输入草稿")
        expected_frame = result.pop("window_frame")
        actual_frame = desktop.pet.frame()
        result["swap_frame"] = {"before": str(expected_frame), "after": str(actual_frame)}
        check(actual_frame == expected_frame, "换装保留窗口位置")
        candidate.fail("验收用加载失败")
        desktop.syncAppearanceState()
        check(candidate.web.isHidden() and len(candidate.view.subviews()) == 1,
              "加载失败保持透明，不自动显示 2D")
        check("验收用加载失败" in desktop.input_feedback, "加载失败向用户显示具体原因")
        finish()

    @guarded
    def debugged(value, error):
        if error:
            raise RuntimeError(error)
        result["renderer"] = json.loads(value)
        check(result["renderer"]["frames"] > 10, "动画帧持续推进")
        check(len(result["renderer"]["behavior"]["idleActions"]) >= 3, "至少三种共享待机动作完成骨骼重定向")
        check(len(result["renderer"]["behavior"]["idleActions"]) == 11, "实际使用原版预制体的十一项待机顺序")
        check(result["renderer"]["lighting"]["key"]["intensity"] == 2.4 and
              result["renderer"]["lighting"]["fill"]["intensity"] == 2.1,
              "实际渲染使用 Web 版主光与补光设置")
        check(not desktop.pet.canBecomeKeyWindow(), "角色窗口不抢键盘焦点")
        check(desktop.drag.hitTest_((80, 90)) == desktop.drag, "拖动与右键事件由原生容器接收")
        idle = Image.open(OUTPUT / "idle.png").convert("RGB")
        speaking = Image.open(OUTPUT / "speaking.png").convert("RGB")
        check(ImageChops.difference(idle, speaking).getbbox() is not None, "说话状态改变实际画面")
        desktop.input.setStringValue_("未发送的换装检查草稿")
        result["window_frame"] = desktop.pet.frame()
        check(all(desktop.menu.itemAtIndex_(i).title() != "形象商店…"
                  for i in range(desktop.menu.numberOfItems())), "菜单没有旧形象商店入口")
        invalid = AK.NSMenuItem.alloc().init()
        invalid.setRepresentedObject_(str(OUTPUT / "missing-vrm"))
        desktop.useLocalVrm_(invalid)
        check(desktop.appearance is appearance and not appearance.closed,
              "新 VRM 加载失败保留当前形象")
        item = AK.NSMenuItem.alloc().init()
        item.setRepresentedObject_(str(alternate))
        desktop.useLocalVrm_(item)
        AppHelper.callLater(0.1, switched)

    def after_speaking():
        if CONTROLS_ONLY:
            appearance.web.evaluateJavaScript_completionHandler_(
                "window.pet.preview('idle', 1)", guarded(lambda _value, _error: verify_drag_bridge()))
            return
        idle_actions = appearance.manifest.get("motionSet", {}).get("idle", [])
        cases = iter([(name, 1.8) for name in idle_actions] + [
            ("drag-left", 0.8), ("drag-right", 0.8), ("drag-still", 2.0), ("release", 0.15),
            ("release", 0.25), ("idle", 1.5)])
        result["motion_gallery"] = []

        @guarded
        def next_case():
            case = next(cases, None)
            if case is None:
                verify_drag_bridge()
                return
            name, seconds = case

            @guarded
            def received(value, error):
                if error:
                    raise RuntimeError(error)
                data = json.loads(value)
                picture = Image.open(io.BytesIO(base64.b64decode(data["image"].split(",", 1)[1])))
                filename = f"motion-{len(result['motion_gallery']):02d}-{name.replace('/', '-')}.png"
                picture.save(OUTPUT / filename)
                bbox = picture.getchannel("A").point(lambda a: 255 if a > 80 else 0).getbbox()
                check(bbox and bbox[0] > 1 and bbox[1] > 1 and bbox[2] < picture.width - 1 and bbox[3] < picture.height - 1,
                      f"{filename}：角色保持在画面内")
                result["motion_gallery"].append({"file": filename, "behavior": data["behavior"]})
                if name in appearance.manifest.get("motionSettings", {}):
                    expected = appearance.manifest["motionSettings"][name]["timeScale"]
                    check(abs(data["behavior"]["actionSpeed"] - expected) < 0.0001,
                          f"{name}：按原版 {expected} 倍速度播放")
                next_case()
            script = f"JSON.stringify(window.pet.preview({json.dumps(name)}, {seconds}))"
            appearance.web.evaluateJavaScript_completionHandler_(script, received)
        next_case()

    def verify_drag_bridge():
        desktop.drag.sendInteraction("drag-start", (900, 0))

        @guarded
        def lifted(value, error):
            if error:
                raise RuntimeError(error)
            b = json.loads(value)["behavior"]
            result["drag_entry"] = b
            check(b["dragging"] and b["action"] == appearance.manifest["motionSet"]["dragging"] and b["actionTime"] > 0 and b["actionLoop"] == 2201,
                  "原生按下消息进入并推进 Drag 动画")
            desktop.drag.sendInteraction("drag-end")

            @guarded
            def landed(value, error):
                if error:
                    raise RuntimeError(error)
                b = json.loads(value)["behavior"]
                result["drag_exit"] = b
                check(not b["dragging"] and b["action"] in b["idleActions"],
                      "原生释放消息过渡回当前待机动作")
                if os.environ.get("BOXAGENT_VRM_RECORD") == "1":
                    record_motion()
                else:
                    appearance.web.evaluateJavaScript_completionHandler_("window.pet.resume(); JSON.stringify(window.pet.debug())", debugged)
            appearance.web.evaluateJavaScript_completionHandler_("JSON.stringify(window.pet.advance(1.5))", landed)
        appearance.web.evaluateJavaScript_completionHandler_("JSON.stringify(window.pet.advance(0.12))", lifted)

    def record_motion():
        frames = []
        font = ImageFont.truetype("/System/Library/Fonts/STHeiti Light.ttc", 18)

        @guarded
        def record(index=0):
            if index >= 184:
                frames[0].save(OUTPUT / "avatar-motion.gif", save_all=True, append_images=frames[1:], duration=63, loop=0)
                appearance.web.evaluateJavaScript_completionHandler_("window.pet.resume(); JSON.stringify(window.pet.debug())", debugged)
                return
            before = ""
            label = "共享动作库 · 自然待机"
            if index == 0:
                first = json.dumps(appearance.manifest["motionSet"]["idle"][0])
                before = f"window.pet.preview('idle',1); window.pet.preview({first},0);"
            elif 56 <= index < 80:
                label = "Drag 动画 · 摆动叠加"
                velocity = 800 if index < 68 else -650
                before = f"window.pet.interact({{kind:'drag-move',velocity:[{velocity},0]}});"
            elif 80 <= index < 104:
                label = "释放 · 过渡回待机"
                if index == 80:
                    before = "window.pet.interact({kind:'drag-end'});"
            elif index >= 104:
                label = "恢复共享待机动画"
                if index == 104:
                    second = json.dumps(appearance.manifest["motionSet"]["idle"][1])
                    before = f"window.pet.preview({second},0);"

            @guarded
            def received(value, error):
                if error:
                    raise RuntimeError(error)
                data = json.loads(value)
                avatar = Image.open(io.BytesIO(base64.b64decode(data["image"].split(",", 1)[1]))).convert("RGBA")
                frame = Image.new("RGB", (max(400, avatar.width + 32), 424), (242, 243, 246))
                frame.paste(avatar, ((frame.width - avatar.width) // 2, 34), avatar)
                draw = ImageDraw.Draw(frame)
                draw.text((frame.width // 2, 20), label, font=font, anchor="mm", fill=(72, 78, 92))
                frames.append(frame)
                record(index + 1)
            appearance.web.evaluateJavaScript_completionHandler_(before + "JSON.stringify(window.pet.advance(0.0625))", received)
        record()

    @guarded
    def speaking():
        desktop.state = Snapshot(speaking=True)
        AppHelper.callLater(1, lambda: capture("speaking", after_speaking))

    @guarded
    def poll():
        if appearance.error:
            raise RuntimeError(appearance.error)
        if appearance.ready:
            desktop.syncAppearanceState()
            check(not desktop.pet.ignoresMouseEvents(), "3D 就绪后恢复原生鼠标交互")
            # 隔离业务轮询，只由本地快照驱动展示。
            desktop.timer.invalidate()
            from Foundation import NSTimer
            desktop.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                1 / 30, True, lambda _timer: appearance.present(desktop.state, time.monotonic()))
            desktop.state = Snapshot()
            desktop.bubble.orderOut_(None)
            AppHelper.callLater(1, lambda: capture("idle", speaking))
        elif time.monotonic() - started > 35:
            raise AssertionError("等待首帧超时")
        else:
            AppHelper.callLater(0.1, poll)
    AppHelper.callLater(0.1, poll)
    AppHelper.runEventLoop()
    if result.get("error"):
        raise SystemExit(1)
