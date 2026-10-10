"""原生桌面宿主：位置、菜单和字幕。角色通过 Appearance 注入。"""

import json
import logging
import math
import os
import time

import AppKit as AK
import objc
from Foundation import (NSObject, NSTimer, NSAttributedString,
                        NSMutableAttributedString, NSUserNotification,
                        NSUserNotificationCenter,
                        NSUserNotificationDefaultSoundName, NSURL)

from boxagent.core.states import Snapshot
from boxagent.agent.harness.persona import update_persona_name
from boxagent.interfaces.macos.hotkey import Hotkey
from boxagent.interfaces.macos.menu import install_menus
from boxagent.interfaces.macos.run_records import SessionRunRecords
from boxagent.interfaces.macos.state import ACTIVE_TASK_STATES, mode_text, transcript_sections
from boxagent.interfaces.macos.windows.conversation import (ConversationPanel, FlippedView, RoundedSurface,
                                   SendButton, button, label, symbol_button)
from boxagent.interfaces.macos.windows.pet import DragView, PetPanel


class Desktop(NSObject):
    @objc.python_method
    def configure(self, backend, appearance, catalog=None, *, data_dir=None,
                  appearance_factory=None, appearance_preparer=None,
                  pet_store_factory=None, memory_dashboard_factory=None,
                  skill_manager_factory=None, persona_settings_factory=None,
                  persona=None,
                  persona_loader=None, soul_file=None,
                  editable_soul_file=None):
        self.backend, self.appearance = backend, appearance
        self.data_dir = data_dir or backend.log_dir
        self.pet_catalog = catalog
        self.appearance_factory = appearance_factory
        self.appearance_preparer = appearance_preparer
        self.pet_store_factory = pet_store_factory
        self.memory_dashboard_factory = memory_dashboard_factory
        self.skill_manager_factory = skill_manager_factory
        self.persona_settings_factory = persona_settings_factory
        self.persona_loader = persona_loader
        self.soul_file = soul_file
        self.editable_soul_file = editable_soul_file or soul_file
        self.agent_name = getattr(persona, "name", None) or "伙伴"
        self.pet_store = None
        self.memory_dashboard = None
        self.skill_manager = None
        self.persona_settings = None
        self.state = Snapshot()
        self.bubble_open = False
        self.last_revision = -1
        self.closing = False
        self.submission = None
        self.pending_user_text = ""
        self.input_feedback = ""
        self.content_signature = None
        self.conversation_events = []
        self.history_request = None
        self.history_request_session = ""
        self.history_refresh_pending = False
        self.context_until = 0
        self.context_reveal_at = 0
        return self

    def applicationDidFinishLaunching_(self, _notification):
        AK.NSApplication.sharedApplication().setActivationPolicy_(AK.NSApplicationActivationPolicyAccessory)
        screen = AK.NSScreen.mainScreen().visibleFrame()
        width, height = self.appearance.size
        x, y = screen.origin.x + screen.size.width - width - 26, screen.origin.y + 30
        try:
            saved = json.loads((self.data_dir / "position.json").read_text())
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
        self.pet.setTitle_(self.agent_name)
        self.makeBubble()
        self.makeContextBubble()
        self.makeMenu()
        if self.pet_catalog is not None:
            self.syncAppearanceName(self.pet_catalog)
        self.pet.orderFrontRegardless()
        self.hotkey = Hotkey(lambda: self.toggleMic_(None))
        if not self.hotkey.registered:
            self.state.error = "快捷键已被占用，请使用菜单或麦克风按钮"
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1 / 30, self, "tick:", None, True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "app.pid").write_text(str(os.getpid()))

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
        self.bubble.setTitle_(self.agent_name)
        view = RoundedSurface.alloc().initWithFrame_(((0, 0), (360, 178)))
        view.kind = "panel"
        self.bubble.setContentView_(view)
        self.bubble_view = view
        self.title_label = label(self.agent_name, ((18, 138), (112, 24)), 15)
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
        self.input.setPlaceholderString_(f"和 {self.agent_name} 说点什么…")
        self.input.setAccessibilityLabel_("对话输入")
        self.input.setTarget_(self)
        self.input.setAction_("submitText:")
        self.input.setDelegate_(self)
        self.input.cell().setUsesSingleLineMode_(True)
        self.input.cell().setScrollable_(True)
        self.input.cell().setSendsActionOnEndEditing_(False)
        self.send_button = symbol_button("arrow.up", "发送 · 回车", ((286, 6), (30, 30)), self, "submitText:", SendButton)
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
        self.menu, self.context_menu_item, self.status_item = install_menus(self)

    def showPetStore_(self, _sender):
        if self.pet_catalog is None or self.pet_store_factory is None:
            self.state.error = "形象商店未配置"
            self.updateLabels()
            return
        if self.pet_store is None:
            self.pet_store = self.pet_store_factory(self, self.pet_catalog)
        self.pet_store.show()

    def showMemoryDashboard_(self, _sender):
        if self.memory_dashboard_factory is None:
            self.state.error = "记忆看板未配置"
            self.updateLabels()
            return
        if self.memory_dashboard is None:
            self.memory_dashboard = self.memory_dashboard_factory(self)
        self.memory_dashboard.show()

    def showSkillManager_(self, _sender):
        if self.skill_manager_factory is None:
            self.state.error = "Skill 管理未配置"
            self.updateLabels()
            return
        if self.skill_manager is None:
            self.skill_manager = self.skill_manager_factory(self)
        self.skill_manager.show()

    def openRunRecords_(self, _sender):
        records = SessionRunRecords(self.data_dir / "runs")
        self._openDirectory(records.materialize_view(self.state.session_id))

    def openLatestRun_(self, _sender):
        records = SessionRunRecords(self.data_dir / "runs")
        latest = records.latest(self.state.session_id)
        self._openDirectory(
            latest or records.materialize_view(self.state.session_id))

    def openDiagnosticLogs_(self, _sender):
        self._openDirectory(self.backend.log_dir)

    @objc.python_method
    def _openDirectory(self, path):
        path.mkdir(parents=True, exist_ok=True)
        AK.NSWorkspace.sharedWorkspace().openURL_(
            NSURL.fileURLWithPath_(str(path)))

    def editSoul_(self, _sender):
        if (self.persona_settings_factory is None
                or self.editable_soul_file is None or self.soul_file is None):
            self.state.error = "角色设定文件未配置"
            self.updateLabels()
            return
        if self.persona_settings is None:
            self.persona_settings = self.persona_settings_factory(
                self, self.soul_file, self.editable_soul_file)
        self.persona_settings.show()

    @objc.python_method
    def reloadPersona(self):
        if self.persona_loader is None or self.soul_file is None:
            return
        try:
            persona = self.persona_loader(self.soul_file)
        except (OSError, RuntimeError, ValueError):
            return
        self.agent_name = persona.name
        self.title_label.setStringValue_(self.agent_name)
        self.input.setPlaceholderString_(f"和 {self.agent_name} 说点什么…")
        self.bubble.setTitle_(self.agent_name)
        self.pet.setTitle_(self.agent_name)
        self.status_item.button().setToolTip_(
            f"{self.agent_name} · ⌃⌥空格切换麦克风")
        self.content_signature = None
        self.updateLabels()

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
        self.syncAppearanceName(catalog)

    @objc.python_method
    def syncAppearanceName(self, catalog):
        """Use the selected appearance name, with a neutral packaged fallback."""
        try:
            directory = self.appearance.directory.resolve()
            name = ("伙伴" if directory == catalog.default_pet
                    else str(self.appearance.manifest.get("displayName") or "伙伴")[:24])
            source = (self.editable_soul_file if self.editable_soul_file.is_file()
                      else self.soul_file)
            content = source.read_text(encoding="utf-8")
            updated = update_persona_name(content, name)
            self.editable_soul_file.parent.mkdir(parents=True, exist_ok=True)
            if (not self.editable_soul_file.is_file()
                    or self.editable_soul_file.read_text(encoding="utf-8") != updated):
                self.editable_soul_file.write_text(updated, encoding="utf-8")
            self.soul_file = self.editable_soul_file
            self.reloadPersona()
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            logging.getLogger(__name__).exception("形象已切换，但角色名称同步失败")

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
        self.backend.submit(self.backend.application.toggle_context())

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

    def createSession_(self, _sender):
        alert = AK.NSAlert.alloc().init()
        alert.setMessageText_("新建会话")
        alert.setInformativeText_("新会话拥有独立的短期上下文；长期记忆仍然跨会话可用。")
        alert.addButtonWithTitle_("创建")
        alert.addButtonWithTitle_("取消")
        title_input = AK.NSTextField.alloc().initWithFrame_(((0, 0), (280, 24)))
        title_input.setPlaceholderString_("例如：产品演示")
        alert.setAccessoryView_(title_input)
        alert.window().setInitialFirstResponder_(title_input)
        if alert.runModal() != AK.NSAlertFirstButtonReturn:
            return
        title = title_input.stringValue().strip() or "新会话"
        self.pending_user_text = ""
        self.input_feedback = "正在创建新会话…"
        self.conversation_events = []
        self.showBubble()
        self.backend.submit(self.backend.application.create_session(title))
        self.updateLabels()

    @objc.python_method
    def savePosition(self):
        point = self.pet.frame().origin
        (self.data_dir / "position.json").write_text(json.dumps([point.x, point.y]))

    def toggleMic_(self, _sender):
        self.showBubble()
        self.backend.submit(self.backend.application.toggle_voice())

    def cancelTask_(self, _sender):
        self.backend.submit(self.backend.application.cancel_task())

    def allow_(self, _sender):
        self.backend.submit(self.backend.application.answer_approval(True))

    def deny_(self, _sender):
        self.backend.submit(self.backend.application.answer_approval(False))

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
        self.pending_user_text = draft
        self.input.setStringValue_("")
        self.input_feedback = ""
        self.submission = self.backend.submit(self.backend.application.submit_text(draft))
        self.updateLabels()

    def tick_(self, _timer):
        changed = False
        if self.history_request and self.history_request.done():
            try:
                events = self.history_request.result()
                if self.history_request_session == self.state.session_id:
                    self.conversation_events = events
            except Exception:
                pass
            self.history_request = None
            if self.history_refresh_pending:
                self.history_refresh_pending = False
                self.requestConversationHistory()
            changed = True
        if self.submission and self.submission.done():
            try:
                result = self.submission.result()
                if result["status"] == "accepted":
                    self.input_feedback = ""
                else:
                    if not self.input.stringValue():
                        self.input.setStringValue_(self.submitted_draft)
                    self.input_feedback = result.get("message", "未能提交，请重试")
            except Exception:
                if not self.input.stringValue():
                    self.input.setStringValue_(self.submitted_draft)
                self.input_feedback = "未能提交，请重试"
            self.pending_user_text = ""
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
                if event["type"] in {
                        "engine.ready", "session.ready", "session.created",
                        "session.activated", "session.archived", "front.user_text", "task.accepted",
                        "conversation.updated"}:
                    if event["type"] in {"session.created", "session.activated",
                                         "session.archived"}:
                        self.input_feedback = ""
                    if (event["type"] in {"session.created", "session.activated",
                                          "session.archived"}
                            and self.history_request_session != self.state.session_id):
                        self.conversation_events = []
                    self.requestConversationHistory()
                if event["type"] in {"task.approval", "task.succeeded", "task.failed",
                                     "task.blocked", "voice.error",
                                     "notification.pending",
                                     "notification.system_requested"}:
                    self.showBubble()
                if event["type"] == "notification.system_requested":
                    channel = "system" if self._show_system_notification(
                        self.state.notification_text) else "ui"
                    self.backend.submit(
                        self.backend.application.acknowledge_notification(
                            self.state.notification_id, channel,
                            "submitted" if channel == "system" else "presented"))
            elif "error" in event:
                self.state.error = event["error"]
            elif event.get("type") == "backend.restarting":
                self.state.error = "后台正在重新加载，完成后会自动恢复连接…"
            elif event.get("type") in {"backend.connected", "backend.reconnected"}:
                if self.state.error.startswith("后台正在重新加载"):
                    self.state.error = ""
                self.reloadPersona()
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
        active = self.state.task in ACTIVE_TASK_STATES
        self.mode_label.setStringValue_(mode_text(self.state))
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
        enabled = bool(self.input.stringValue().strip()) and self.submission is None
        self.send_button.setEnabled_(enabled)
        self.send_button.setContentTintColor_(AK.NSColor.whiteColor() if enabled else AK.NSColor.tertiaryLabelColor())
        self.send_button.setNeedsDisplay_(True)
        self.updateTranscript()
        self.status_item.button().setTitle_("●" if self.state.voice != "off" else "◉")
        self.last_revision = self.state.revision
        (self.data_dir / "ui.json").write_text(json.dumps({**self.state.payload(), "pet_window": self.pet.windowNumber(),
            "bubble_window": self.bubble.windowNumber(), "pid": os.getpid()}, ensure_ascii=False), encoding="utf-8")

    @objc.python_method
    def _show_system_notification(self, text):
        try:
            notification = NSUserNotification.alloc().init()
            notification.setTitle_(f"{self.agent_name} 任务提醒")
            notification.setInformativeText_(text or "后台任务已经结束")
            notification.setSoundName_(NSUserNotificationDefaultSoundName)
            NSUserNotificationCenter.defaultUserNotificationCenter().deliverNotification_(
                notification)
            return True
        except Exception:
            return False

    @objc.python_method
    def updateTranscript(self):
        sections = transcript_sections(
            self.state, self.input_feedback, history=self.conversation_events,
            assistant_name=self.agent_name,
            pending_user_text=self.pending_user_text)
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

    @objc.python_method
    def requestConversationHistory(self):
        session_id = self.state.session_id
        if not session_id:
            return
        if self.history_request is not None:
            self.history_refresh_pending = True
            return
        self.history_request_session = session_id
        self.history_request = self.backend.submit(
            self.backend.application.session_events(session_id, limit=240))

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
            (self.data_dir / "app.pid").unlink(missing_ok=True)
        return AK.NSTerminateNow
