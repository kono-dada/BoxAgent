"""Native editor for the user-owned persona configuration."""

import AppKit as AK
import objc
from Foundation import NSObject

from boxagent.agent.harness.persona import persona_name, update_persona_name
from boxagent.interfaces.macos.windows.conversation import button, label


class PersonaSettingsWindow(NSObject):
    """Edit the persona in-app and apply it without opening another program."""

    @objc.python_method
    def configure(self, owner, source_file, target_file):
        self.owner = owner
        self.source_file = source_file
        self.target_file = target_file

        style = (AK.NSWindowStyleMaskTitled | AK.NSWindowStyleMaskClosable
                 | AK.NSWindowStyleMaskMiniaturizable)
        self.window = AK.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (700, 610)), style, AK.NSBackingStoreBuffered, False)
        self.window.setTitle_("角色设定")
        self.window.setReleasedWhenClosed_(False)
        self.window.center()
        root = self.window.contentView()

        root.addSubview_(label("角色设定", ((24, 560), (300, 30)), 21))
        root.addSubview_(label(
            "名称会显示在对话中；SOUL.md 决定角色的性格、语气和互动方式。",
            ((24, 536), (650, 20)), 12, AK.NSColor.secondaryLabelColor()))

        root.addSubview_(label("角色名称", ((24, 492), (90, 22)), 13))
        self.name = AK.NSTextField.alloc().initWithFrame_(((116, 488), (558, 28)))
        self.name.setPlaceholderString_("例如：小芽")
        self.name.setAccessibilityLabel_("角色名称")

        root.addSubview_(label("SOUL.md", ((24, 452), (100, 22)), 13))
        self.editor = AK.NSTextView.alloc().initWithFrame_(((0, 0), (646, 350)))
        self.editor.setFont_(AK.NSFont.monospacedSystemFontOfSize_weight_(
            12, AK.NSFontWeightRegular))
        self.editor.setRichText_(False)
        self.editor.setAutomaticQuoteSubstitutionEnabled_(False)
        self.editor.setAutomaticDashSubstitutionEnabled_(False)
        self.editor.setTextContainerInset_((10, 10))
        self.editor.setAccessibilityLabel_("角色设定内容")
        scroll = AK.NSScrollView.alloc().initWithFrame_(((24, 84), (650, 364)))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(AK.NSBezelBorder)
        scroll.setDocumentView_(self.editor)

        self.status = label("", ((24, 28), (430, 30)), 11,
                            AK.NSColor.secondaryLabelColor())
        cancel = button("取消", ((492, 24), (82, 32)), self, "cancel:")
        self.save_button = button(
            "保存并应用", ((580, 24), (96, 32)), self, "save:")
        self.save_button.setKeyEquivalent_("\r")

        for view in (self.name, scroll, self.status, cancel, self.save_button):
            root.addSubview_(view)
        return self

    def show(self):
        self._load()
        self.window.makeKeyAndOrderFront_(None)
        AK.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeFirstResponder_(self.name)

    @objc.python_method
    def _load(self):
        source = self.target_file if self.target_file.is_file() else self.source_file
        try:
            content = source.read_text(encoding="utf-8")
        except OSError:
            content = ""
        self.name.setStringValue_(persona_name(content))
        self.editor.setString_(content)
        self.status.setStringValue_("")

    def cancel_(self, _sender):
        self.window.orderOut_(None)

    def save_(self, _sender):
        try:
            content = update_persona_name(
                self.editor.string(), self.name.stringValue())
            self.target_file.parent.mkdir(parents=True, exist_ok=True)
            self.target_file.write_text(content, encoding="utf-8")
            self.owner.soul_file = self.target_file
            self.owner.reloadPersona()
        except (OSError, ValueError) as exc:
            self.status.setTextColor_(AK.NSColor.systemRedColor())
            self.status.setStringValue_(str(exc) or "角色设定保存失败")
            return
        self.editor.setString_(content)
        self.status.setTextColor_(AK.NSColor.systemGreenColor())
        self.status.setStringValue_("已应用；后台正在同步新的角色设定")
