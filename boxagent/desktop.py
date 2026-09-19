"""原生桌面宿主：位置、菜单和字幕。角色通过 Appearance 注入。"""

import asyncio
import concurrent.futures
import json
import math
import os
import queue
import threading
import time
import traceback

import AppKit as AK
import objc
from Foundation import NSObject, NSTimer, NSAttributedString, NSMutableAttributedString

from .config import DATA, LOG_DIR
from .domain import Snapshot
from .hotkey import Hotkey
from .diagnostics import redact


class Backend:
    def __init__(self, runtime_factory):
        self.events = queue.SimpleQueue()
        self.loop = asyncio.new_event_loop()
        self.runtime = runtime_factory(self.publish)
        self.publish({"type": "app.started", "occurred_at": time.time(), "pid": os.getpid()})
        self.thread = threading.Thread(target=self.run, name="BoxAgent-backend", daemon=True)
        self.thread.start()

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.set_exception_handler(lambda _loop, context: self.publish({
            "type": "backend.error", "occurred_at": time.time(),
            "error": "后台出现异常，请重启后重试", "detail": repr(context.get("exception") or context.get("message"))}))
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.close()

    def publish(self, event):
        self.events.put(event)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "events.jsonl").open("a", encoding="utf-8") as stream:
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
                              "error": "请求未能处理，请稍后重试", "detail": repr(exc), "traceback": traceback.format_exc()})
        future.add_done_callback(finished)
        return future

    def close(self):
        try:
            asyncio.run_coroutine_threadsafe(self.runtime.close(), self.loop).result(timeout=8)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
        self.publish({"type": "app.stopped", "occurred_at": time.time(), "pid": os.getpid()})


class PetPanel(AK.NSPanel):
    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class ConversationPanel(PetPanel):
    def canBecomeKeyWindow(self):
        # 只有对话面板接收文字焦点，小鸭本身仍不会抢占键盘。
        return True


class FlippedView(AK.NSView):
    def isFlipped(self):
        return True


class RoundedSurface(AK.NSView):
    kind = objc.ivar()

    def drawRect_(self, _rect):
        dark = self.effectiveAppearance().bestMatchFromAppearancesWithNames_(
            [AK.NSAppearanceNameAqua, AK.NSAppearanceNameDarkAqua]) == AK.NSAppearanceNameDarkAqua
        radius = 16 if self.kind == "panel" else 10
        path = AK.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), radius, radius)
        if self.kind == "panel":
            color = (AK.NSColor.colorWithSRGBRed_green_blue_alpha_(.13, .14, .16, 1) if dark
                     else AK.NSColor.colorWithSRGBRed_green_blue_alpha_(.97, .97, .985, 1))
        else:
            color = AK.NSColor.textBackgroundColor()
        color.setFill()
        path.fill()
        if self.kind != "panel":
            AK.NSColor.separatorColor().setStroke()
            path.setLineWidth_(.5)
            path.stroke()

    def viewDidChangeEffectiveAppearance(self):
        self.setNeedsDisplay_(True)


class SendButton(AK.NSButton):
    def drawRect_(self, rect):
        color = (AK.NSColor.colorWithSRGBRed_green_blue_alpha_(.29, .34, .78, 1)
                 if self.isEnabled() else AK.NSColor.quaternaryLabelColor())
        color.setFill()
        AK.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 8, 8).fill()
        objc.super(SendButton, self).drawRect_(rect)


class DragView(AK.NSView):
    owner = objc.ivar()

    def hitTest_(self, point):
        return self if AK.NSPointInRect(point, self.frame()) else None

    def mouseDown_(self, event):
        self.down = AK.NSEvent.mouseLocation()
        self.origin = self.window().frame().origin
        self.dragged = False

    def mouseDragged_(self, event):
        point = AK.NSEvent.mouseLocation()
        dx, dy = point.x - self.down.x, point.y - self.down.y
        if abs(dx) + abs(dy) > 4:
            self.dragged = True
            self.window().setFrameOrigin_((self.origin.x + dx, self.origin.y + dy))
            self.owner.positionBubble()

    def mouseUp_(self, event):
        if not self.dragged:
            self.owner.toggleBubble_(None)
        self.owner.savePosition()

    def rightMouseDown_(self, event):
        AK.NSMenu.popUpContextMenu_withEvent_forView_(self.owner.menu, event, self)


def label(text, frame, size=13, color=None):
    view = AK.NSTextField.labelWithString_(text)
    view.setFrame_(frame)
    view.setFont_(AK.NSFont.systemFontOfSize_(size))
    view.setTextColor_(color or AK.NSColor.labelColor())
    view.setLineBreakMode_(AK.NSLineBreakByWordWrapping)
    view.setMaximumNumberOfLines_(0)
    return view


def button(title, frame, target, action):
    view = AK.NSButton.buttonWithTitle_target_action_(title, target, action)
    view.setFrame_(frame)
    view.setBezelStyle_(AK.NSBezelStyleRounded)
    return view


def symbol_button(name, description, frame, target, action, button_class=AK.NSButton):
    view = button_class.buttonWithTitle_target_action_("", target, action)
    view.setFrame_(frame)
    view.setBordered_(False)
    view.setImage_(AK.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, description))
    view.setImagePosition_(AK.NSImageOnly)
    view.setToolTip_(description)
    view.setAccessibilityLabel_(description)
    return view


class Desktop(NSObject):
    @objc.python_method
    def configure(self, backend, appearance, catalog=None):
        self.backend, self.appearance = backend, appearance
        self.pet_catalog = catalog
        self.pet_store = None
        self.state = Snapshot()
        self.bubble_open = False
        self.last_revision = -1
        self.closing = False
        self.submission = None
        self.input_feedback = ""
        self.content_signature = None
        self.context_until = 0
        self.context_reveal_at = 0
        return self

    def applicationDidFinishLaunching_(self, _notification):
        AK.NSApplication.sharedApplication().setActivationPolicy_(AK.NSApplicationActivationPolicyAccessory)
        screen = AK.NSScreen.mainScreen().visibleFrame()
        width, height = self.appearance.size
        x, y = screen.origin.x + screen.size.width - width - 26, screen.origin.y + 30
        try:
            saved = json.loads((DATA / "position.json").read_text())
            if screen.origin.x <= saved[0] <= screen.origin.x + screen.size.width - width:
                x, y = saved
                y = max(screen.origin.y, min(y, screen.origin.y + screen.size.height - height))
        except (OSError, ValueError, TypeError):
            pass
        style = AK.NSWindowStyleMaskBorderless | AK.NSWindowStyleMaskNonactivatingPanel
        self.pet = PetPanel.alloc().initWithContentRect_styleMask_backing_defer_(((x, y), (width, height)), style, AK.NSBackingStoreBuffered, False)
        self.preparePanel(self.pet)
        self.pet.setHasShadow_(False)
        self.drag = DragView.alloc().initWithFrame_(((0, 0), (width, height)))
        self.drag.owner = self
        self.drag.addSubview_(self.appearance.view)
        self.pet.setContentView_(self.drag)
        self.pet.setTitle_("BoxAgent 桌宠")
        self.makeBubble()
        self.makeContextBubble()
        self.makeMenu()
        self.pet.orderFrontRegardless()
        self.hotkey = Hotkey(lambda: self.toggleMic_(None))
        if not self.hotkey.registered:
            self.state.error = "快捷键已被占用，请使用菜单或麦克风按钮"
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1 / 30, self, "tick:", None, True)
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "app.pid").write_text(str(os.getpid()))
        self.showBubble()

    @objc.python_method
    def preparePanel(self, panel):
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AK.NSColor.clearColor())
        panel.setLevel_(AK.NSFloatingWindowLevel)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(AK.NSWindowCollectionBehaviorCanJoinAllSpaces | AK.NSWindowCollectionBehaviorFullScreenAuxiliary)
        panel.setReleasedWhenClosed_(False)

    @objc.python_method
    def makeBubble(self):
        self.bubble = ConversationPanel.alloc().initWithContentRect_styleMask_backing_defer_(((0, 0), (360, 178)),
            AK.NSWindowStyleMaskBorderless | AK.NSWindowStyleMaskNonactivatingPanel, AK.NSBackingStoreBuffered, False)
        self.preparePanel(self.bubble)
        self.bubble.setBecomesKeyOnlyIfNeeded_(True)
        self.bubble.setTitle_("BoxAgent 对话")
        view = RoundedSurface.alloc().initWithFrame_(((0, 0), (360, 178)))
        view.kind = "panel"
        self.bubble.setContentView_(view)
        self.bubble_view = view
        self.title_label = label("BoxAgent", ((18, 138), (112, 24)), 15)
        self.title_label.setFont_(AK.NSFont.systemFontOfSize_weight_(15, AK.NSFontWeightSemibold))
        self.mode_label = label("", ((139, 140), (166, 20)), 11, AK.NSColor.secondaryLabelColor())
        self.mode_label.setAlignment_(AK.NSTextAlignmentRight)
        self.close_button = symbol_button("xmark", "收起对话", ((316, 138), (28, 28)), self, "toggleBubble:")
        self.close_button.setContentTintColor_(AK.NSColor.secondaryLabelColor())

        self.transcript = AK.NSTextView.alloc().initWithFrame_(((0, 0), (328, 24)))
        self.transcript.setEditable_(False)
        self.transcript.setSelectable_(True)
        self.transcript.setDrawsBackground_(False)
        self.transcript.setTextContainerInset_((0, 0))
        self.transcript.textContainer().setLineFragmentPadding_(0)
        self.transcript.setVerticallyResizable_(True)
        self.transcript.setHorizontallyResizable_(False)
        self.transcript.setAutoresizingMask_(AK.NSViewWidthSizable)
        self.transcript.textContainer().setWidthTracksTextView_(True)
        self.transcript_scroll = AK.NSScrollView.alloc().initWithFrame_(((16, 104), (328, 24)))
        self.transcript_scroll.setDrawsBackground_(False)
        self.transcript_scroll.setBorderType_(AK.NSNoBorder)
        self.transcript_scroll.setHasVerticalScroller_(True)
        self.transcript_scroll.setAutohidesScrollers_(True)
        self.transcript_scroll.setScrollerStyle_(AK.NSScrollerStyleOverlay)
        self.transcript_scroll.setDocumentView_(self.transcript)

        self.composer = RoundedSurface.alloc().initWithFrame_(((16, 50), (328, 42)))
        self.composer.kind = "input"
        self.input = AK.NSTextField.alloc().initWithFrame_(((12, 10), (263, 22)))
        self.input.setFont_(AK.NSFont.systemFontOfSize_(13))
        self.input.setBordered_(False)
        self.input.setDrawsBackground_(False)
        self.input.setFocusRingType_(AK.NSFocusRingTypeNone)
        self.input.setPlaceholderString_("输入要完成的事情…")
        self.input.setAccessibilityLabel_("任务输入")
        self.input.setTarget_(self)
        self.input.setAction_("submitText:")
        self.input.setDelegate_(self)
        self.input.cell().setUsesSingleLineMode_(True)
        self.input.cell().setScrollable_(True)
        self.input.cell().setSendsActionOnEndEditing_(False)
        self.send_button = symbol_button("arrow.up", "执行任务 · 回车", ((286, 6), (30, 30)), self, "submitText:", SendButton)
        self.composer.addSubview_(self.input)
        self.composer.addSubview_(self.send_button)

        self.mic_button = button("语音", ((12, 10), (78, 28)), self, "toggleMic:")
        self.mic_button.setBordered_(False)
        self.mic_button.setImagePosition_(AK.NSImageLeading)
        self.mic_button.setToolTip_("开启或关闭语音 · Control + Option + 空格")
        self.shortcut_label = label("⌃⌥ 空格", ((94, 15), (80, 18)), 11, AK.NSColor.tertiaryLabelColor())
        self.cancel_button = button("停止", ((272, 10), (74, 28)), self, "cancelTask:")
        self.cancel_button.setBordered_(False)
        self.allow_button = button("允许操作", ((179, 10), (100, 28)), self, "allow:")
        self.deny_button = button("拒绝", ((279, 10), (67, 28)), self, "deny:")
        for item in (self.title_label, self.mode_label, self.close_button, self.transcript_scroll,
                     self.composer, self.mic_button, self.shortcut_label, self.cancel_button,
                     self.allow_button, self.deny_button):
            view.addSubview_(item)
        self.allow_button.setHidden_(True)
        self.deny_button.setHidden_(True)

    @objc.python_method
    def makeMenu(self):
        # 原生编辑菜单为输入框提供复制、粘贴和全选，不需要全局键盘监听。
        main_menu = AK.NSMenu.alloc().initWithTitle_("BoxAgent")
        edit_item = AK.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("编辑", None, "")
        edit_menu = AK.NSMenu.alloc().initWithTitle_("编辑")
        for title, action, key in [("撤销", "undo:", "z"), ("剪切", "cut:", "x"),
                                   ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")]:
            edit_menu.addItem_(AK.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key))
        edit_item.setSubmenu_(edit_menu)
        main_menu.addItem_(edit_item)
        AK.NSApplication.sharedApplication().setMainMenu_(main_menu)
        self.menu = AK.NSMenu.alloc().initWithTitle_("BoxAgent")
        for title, selector in [("显示／收起对话", "toggleBubble:"), ("开启／关闭麦克风  ⌃⌥空格", "toggleMic:"),
                                ("形象商店…", "showPetStore:"),
                                ("开启屏幕总结", "toggleContext:"),
                                ("停止后台任务", "cancelTask:"), ("退出 BoxAgent", "quit:")]:
            item = AK.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, selector, "")
            item.setTarget_(self)
            self.menu.addItem_(item)
            if selector == "toggleContext:":
                self.context_menu_item = item
        self.status_item = AK.NSStatusBar.systemStatusBar().statusItemWithLength_(AK.NSVariableStatusItemLength)
        self.status_item.button().setTitle_("◉")
        self.status_item.button().setToolTip_("BoxAgent · ⌃⌥空格切换麦克风")
        self.status_item.setMenu_(self.menu)

    def showPetStore_(self, _sender):
        from .pets.catalog import PetCatalog
        from .pets.window import PetStoreWindow
        if self.pet_catalog is None:
            self.pet_catalog = PetCatalog()
        if self.pet_store is None:
            self.pet_store = PetStoreWindow.alloc().init().configure(self, self.pet_catalog)
        self.pet_store.show()

    @objc.python_method
    def replaceAppearance(self, appearance, catalog):
        """先验证视图并落盘；任一步失败都保留正在显示的形象和运行状态。"""
        previous = self.appearance
        if appearance.size != previous.size:
            raise ValueError("新形象的显示尺寸不兼容")
        appearance.present(self.state, time.monotonic())
        self.drag.addSubview_(appearance.view)
        try:
            catalog.commit(appearance.directory)
        except Exception:
            appearance.view.removeFromSuperview()
            raise
        previous.view.removeFromSuperview()
        self.appearance = appearance

    @objc.python_method
    def makeContextBubble(self):
        self.context_bubble = PetPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (300, 104)), AK.NSWindowStyleMaskBorderless | AK.NSWindowStyleMaskNonactivatingPanel,
            AK.NSBackingStoreBuffered, False)
        self.preparePanel(self.context_bubble)
        self.context_bubble.setIgnoresMouseEvents_(False)
        self.context_bubble.setTitle_("BoxAgent 桌面观察")
        surface = RoundedSurface.alloc().initWithFrame_(((0, 0), (300, 104)))
        surface.kind = "panel"
        self.context_heading = label("", ((14, 76), (272, 16)), 10, AK.NSColor.secondaryLabelColor())
        self.context_label = label("", ((14, 12), (272, 58)), 12)
        self.context_label.setMaximumNumberOfLines_(0)
        self.context_label.setLineBreakMode_(AK.NSLineBreakByWordWrapping)
        self.context_scroll = AK.NSScrollView.alloc().initWithFrame_(((14, 12), (272, 58)))
        self.context_scroll.setDrawsBackground_(False)
        self.context_scroll.setBorderType_(AK.NSNoBorder)
        self.context_scroll.setHasVerticalScroller_(True)
        self.context_scroll.setAutohidesScrollers_(True)
        self.context_scroll.setScrollerStyle_(AK.NSScrollerStyleOverlay)
        self.context_document = FlippedView.alloc().initWithFrame_(((0, 0), (272, 58)))
        self.context_document.addSubview_(self.context_label)
        self.context_scroll.setDocumentView_(self.context_document)
        surface.addSubview_(self.context_heading)
        surface.addSubview_(self.context_scroll)
        self.context_bubble.setContentView_(surface)

    def toggleContext_(self, _sender):
        self.backend.submit(self.backend.runtime.toggle_context())

    @objc.python_method
    def positionBubble(self):
        frame = self.pet.frame()
        screen = (self.pet.screen() or AK.NSScreen.mainScreen()).visibleFrame()
        width, height = self.bubble.frame().size
        x = min(max(screen.origin.x + 8, frame.origin.x + frame.size.width - width), screen.origin.x + screen.size.width - width - 8)
        y = frame.origin.y + frame.size.height - 10
        if y + height > screen.origin.y + screen.size.height:
            y = max(screen.origin.y + 8, frame.origin.y - height + 10)
        self.bubble.setFrameOrigin_((x, y))

    @objc.python_method
    def showBubble(self):
        self.bubble_open = True
        self.positionBubble()
        self.bubble.orderFrontRegardless()

    def toggleBubble_(self, _sender):
        if self.bubble_open:
            self.bubble.orderOut_(None)
            self.bubble_open = False
        else:
            self.showBubble()

    @objc.python_method
    def savePosition(self):
        point = self.pet.frame().origin
        (DATA / "position.json").write_text(json.dumps([point.x, point.y]))

    def toggleMic_(self, _sender):
        self.showBubble()
        self.backend.submit(self.backend.runtime.toggle_voice())

    def cancelTask_(self, _sender):
        self.backend.submit(self.backend.runtime.cancel_task())

    def allow_(self, _sender):
        self.backend.submit(self.backend.runtime.answer_approval(True))

    def deny_(self, _sender):
        self.backend.submit(self.backend.runtime.answer_approval(False))

    def controlTextDidChange_(self, _notification):
        self.input_feedback = ""
        self.updateLabels()

    def control_textView_doCommandBySelector_(self, _control, _text_view, selector):
        if selector == "cancelOperation:":
            self.toggleBubble_(None)
            return True
        return False

    def submitText_(self, _sender):
        editor = self.input.currentEditor()
        if editor and editor.hasMarkedText():
            return
        draft = self.input.stringValue()
        if not draft.strip() or self.submission:
            return
        self.submitted_draft = draft
        self.submission = self.backend.submit(self.backend.runtime.submit_text(draft))
        self.updateLabels()

    def tick_(self, _timer):
        changed = False
        if self.submission and self.submission.done():
            try:
                result = self.submission.result()
                if result["status"] == "accepted":
                    if self.input.stringValue() == self.submitted_draft:
                        self.input.setStringValue_("")
                    self.input_feedback = ""
                else:
                    self.input_feedback = result.get("message", "未能提交，请重试")
            except Exception:
                self.input_feedback = "未能提交，请重试"
            self.submission = None
            changed = True
        while not self.backend.events.empty():
            event = self.backend.events.get()
            if "state" in event:
                self.state = Snapshot(**event["state"])
                if event["type"] == "context.updated":
                    stamp = time.strftime("%H:%M:%S", time.localtime(self.state.context_at))
                    self.context_heading.setStringValue_(f"桌面观察 · {self.state.context_app} · {stamp}")
                    self.context_label.setStringValue_(self.state.context_text)
                    text_height = max(18, math.ceil(self.context_label.cell().cellSizeForBounds_(
                        ((0, 0), (264, 1000000))).height) + 8)
                    self.context_label.setStringValue_("")
                    screen = (self.pet.screen() or AK.NSScreen.mainScreen()).visibleFrame()
                    visible_height = min(text_height, max(80, screen.size.height * .5 - 46))
                    self.context_bubble.setContentSize_((300, visible_height + 46))
                    self.context_heading.setFrame_(((14, visible_height + 18), (272, 16)))
                    self.context_scroll.setFrame_(((14, 12), (272, visible_height)))
                    self.context_document.setFrameSize_((272, text_height))
                    self.context_label.setFrame_(((0, 0), (264, text_height)))
                    self.context_document.scrollPoint_((0, 0))
                    self.context_reveal_at = time.monotonic()
                    self.context_until = self.context_reveal_at + 2 + max(8, len(self.state.context_text) / 8)
                elif event["type"] in {"context.paused", "context.stale", "context.waiting", "context.error"}:
                    self.context_until = 0
                if event["type"] in {"task.approval", "task.succeeded", "task.failed", "task.blocked", "voice.error"}:
                    self.showBubble()
            elif "error" in event:
                self.state.error = event["error"]
            changed = True
        if changed or self.last_revision < 0:
            self.updateLabels()
        if self.context_until and self.context_bubble.isVisible() and AK.NSPointInRect(
                AK.NSEvent.mouseLocation(), self.context_bubble.frame()):
            self.context_until = max(self.context_until, time.monotonic() + 8)
        if self.context_until > time.monotonic() and not self.bubble_open:
            progress = min(1, max(0, (time.monotonic() - self.context_reveal_at) / 2))
            count = int(len(self.state.context_text) * progress)
            self.context_label.setStringValue_(self.state.context_text[:count])
            frame = self.pet.frame()
            screen = (self.pet.screen() or AK.NSScreen.mainScreen()).visibleFrame()
            x = max(screen.origin.x + 8, min(frame.origin.x + frame.size.width - 300,
                    screen.origin.x + screen.size.width - 308))
            y = frame.origin.y + frame.size.height
            height = self.context_bubble.frame().size.height
            if y + height > screen.origin.y + screen.size.height:
                y = max(screen.origin.y + 8, frame.origin.y - height - 8)
            self.context_bubble.setFrameOrigin_((x, y))
            self.context_bubble.orderFrontRegardless()
        else:
            self.context_bubble.orderOut_(None)
        point, frame = AK.NSEvent.mouseLocation(), self.pet.frame()
        pointer = (point.x - frame.origin.x - frame.size.width / 2, point.y - frame.origin.y - frame.size.height / 2)
        self.appearance.present(self.state, time.monotonic(), pointer)

    @objc.python_method
    def updateLabels(self):
        self.context_menu_item.setTitle_("关闭屏幕总结" if self.state.context_enabled else "开启屏幕总结")
        modes = {"off": "", "connecting": "连接语音中", "ready": "聆听中", "stopping": "关闭语音中"}
        task_modes = {"accepted": "准备中", "running": "执行中", "awaiting_approval": "等待授权",
                      "cancelling": "停止中", "succeeded": "已结束 · 完成", "failed": "已结束 · 未完成",
                      "blocked": "已结束 · 受阻", "cancelled": "已停止"}
        active = self.state.task in {"accepted", "running", "awaiting_approval", "cancelling"}
        mode = "回复中" if self.state.speaking else modes.get(self.state.voice, "")
        self.mode_label.setStringValue_(" · ".join(filter(None, [task_modes.get(self.state.task, ""), mode])))
        self.mic_button.setTitle_("结束语音" if self.state.voice != "off" else "语音")
        self.mic_button.setImage_(AK.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "mic.fill" if self.state.voice != "off" else "mic", "语音"))
        self.mic_button.setEnabled_(self.state.voice not in {"connecting", "stopping"})
        approval = bool(self.state.approval)
        self.cancel_button.setHidden_(approval or not active)
        self.shortcut_label.setHidden_(approval)
        self.allow_button.setHidden_(not approval)
        self.deny_button.setHidden_(not approval)
        self.cancel_button.setEnabled_(self.state.task != "cancelling")
        enabled = bool(self.input.stringValue().strip()) and not active and self.submission is None
        self.send_button.setEnabled_(enabled)
        self.send_button.setContentTintColor_(AK.NSColor.whiteColor() if enabled else AK.NSColor.tertiaryLabelColor())
        self.send_button.setNeedsDisplay_(True)
        self.updateTranscript()
        self.status_item.button().setTitle_("●" if self.state.voice != "off" else "◉")
        self.last_revision = self.state.revision
        (DATA / "ui.json").write_text(json.dumps({**self.state.payload(), "pet_window": self.pet.windowNumber(),
            "bubble_window": self.bubble.windowNumber(), "pid": os.getpid()}, ensure_ascii=False), encoding="utf-8")

    @objc.python_method
    def updateTranscript(self):
        sections = []
        if self.state.context_text:
            stamp = time.strftime("%H:%M:%S", time.localtime(self.state.context_at))
            sections.append((f"桌面观察 · {self.state.context_app} · {stamp}", self.state.context_text))
        elif self.state.context_status:
            sections.append(("桌面观察", self.state.context_status))
        if self.state.user_text:
            sections.append(("你", self.state.user_text))
        if self.state.assistant_text:
            sections.append(("BoxAgent", self.state.assistant_text))
        if self.state.approval:
            sections.append(("需要授权", self.state.approval))
        elif self.state.error:
            sections.append(("提示", self.state.error))
        elif self.state.task_text and self.state.task_text != self.state.assistant_text:
            sections.append(("任务", self.state.task_text))
        if self.state.task_started_at:
            end = self.state.task_ended_at or time.time()
            elapsed = max(0, int(end - self.state.task_started_at))
            if self.state.task_ended_at:
                sections.append(("状态", f"执行已结束 · 用时 {elapsed} 秒"))
            else:
                quiet = max(0, int(time.time() - self.state.task_activity_at))
                status = f"已运行 {elapsed} 秒"
                if quiet >= 15:
                    status += f" · {quiet} 秒未收到新进展，可停止任务"
                sections.append(("状态", status))
        if self.input_feedback:
            sections.append(("提示", self.input_feedback))
        signature = tuple(sections)
        if signature == self.content_signature:
            return
        self.content_signature = signature
        content = NSMutableAttributedString.alloc().initWithString_("")
        def append(text, size, color, spacing=0):
            paragraph = AK.NSMutableParagraphStyle.alloc().init()
            paragraph.setParagraphSpacing_(spacing)
            paragraph.setLineSpacing_(3)
            content.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(text, {
                AK.NSFontAttributeName: AK.NSFont.systemFontOfSize_(size),
                AK.NSForegroundColorAttributeName: color, AK.NSParagraphStyleAttributeName: paragraph}))
        for index, (role, text) in enumerate(sections):
            append(role + "\n", 10, AK.NSColor.secondaryLabelColor(), 3)
            append(text + ("\n" if index < len(sections) - 1 else ""), 13, AK.NSColor.labelColor(), 10)
        if not sections:
            append("有什么需要我帮忙？", 13, AK.NSColor.secondaryLabelColor())
        self.transcript.textStorage().setAttributedString_(content)
        self.transcript.textContainer().setContainerSize_((328, 1000000))
        manager = self.transcript.layoutManager()
        manager.ensureLayoutForTextContainer_(self.transcript.textContainer())
        text_height = max(22, manager.usedRectForTextContainer_(self.transcript.textContainer()).size.height + 4)
        visible_height = min(172, text_height)
        height = visible_height + 156
        self.bubble.setContentSize_((360, height))
        self.bubble_view.setFrame_(((0, 0), (360, height)))
        self.title_label.setFrameOrigin_((18, height - 40))
        self.mode_label.setFrameOrigin_((139, height - 38))
        self.close_button.setFrameOrigin_((316, height - 40))
        self.transcript_scroll.setFrame_(((16, 104), (328, visible_height)))
        self.transcript.setFrameSize_((328, text_height))
        self.transcript.scrollRangeToVisible_((content.length(), 0))
        self.positionBubble()

    def quit_(self, _sender):
        AK.NSApplication.sharedApplication().terminate_(None)

    def applicationShouldTerminate_(self, _sender):
        if not self.closing:
            self.closing = True
            self.hotkey.close()
            self.timer.invalidate()
            if self.pet_store is not None:
                self.pet_store.shutdown()
            self.savePosition()
            self.backend.close()
            (DATA / "app.pid").unlink(missing_ok=True)
        return AK.NSTerminateNow
