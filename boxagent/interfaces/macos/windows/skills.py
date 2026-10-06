"""Native Skill catalog editor backed exclusively by Engine IPC."""

import AppKit as AK
import objc
from Foundation import NSIndexSet, NSObject, NSAttributedString

from boxagent.interfaces.macos.windows.conversation import label


def _text_field(frame):
    field = AK.NSTextField.alloc().initWithFrame_(frame)
    field.setDrawsBackground_(True)
    # Cached AppKit rendering can resolve semantic foreground/background colors
    # from different appearances, producing white-on-white fields. Keep these
    # editor rows explicitly dark so both live windows and offscreen UI checks
    # remain readable in light and dark system modes.
    field.setBackgroundColor_(AK.NSColor.colorWithWhite_alpha_(0.16, 1))
    field.setTextColor_(AK.NSColor.whiteColor())
    return field


def _button(title, frame, target, action, color):
    view = AK.NSButton.buttonWithTitle_target_action_(title, target, action)
    view.setFrame_(frame)
    view.setBezelStyle_(AK.NSBezelStyleRounded)
    view.setBezelColor_(color)
    view.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
        title, {
            AK.NSFontAttributeName: AK.NSFont.systemFontOfSize_weight_(
                12, AK.NSFontWeightMedium),
            AK.NSForegroundColorAttributeName: AK.NSColor.whiteColor(),
        }))
    return view


class SkillManagerWindow(NSObject):
    @objc.python_method
    def configure(self, owner):
        self.owner = owner
        self.backend = owner.backend
        self.items = []
        self.selected = None
        self.generation = 0
        self.saving = False

        style = (AK.NSWindowStyleMaskTitled | AK.NSWindowStyleMaskClosable
                 | AK.NSWindowStyleMaskMiniaturizable)
        self.window = AK.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (860, 620)), style, AK.NSBackingStoreBuffered, False)
        self.window.setTitle_("BoxAgent Skill 管理")
        self.window.setReleasedWhenClosed_(False)
        self.window.center()
        root = self.window.contentView()

        root.addSubview_(label("Skills", ((20, 574), (220, 28)), 20))
        root.addSubview_(label(
            "只启用这里允许的 Skill；内置 Skill 可启停但不可编辑。",
            ((20, 550), (810, 20)), 12, AK.NSColor.secondaryLabelColor()))

        self.table = AK.NSTableView.alloc().initWithFrame_(((0, 0), (250, 470)))
        column = AK.NSTableColumn.alloc().initWithIdentifier_("skill")
        column.setTitle_("Skill")
        column.setWidth_(248)
        self.table.addTableColumn_(column)
        self.table.setHeaderView_(None)
        self.table.setRowHeight_(42)
        self.table.setUsesAlternatingRowBackgroundColors_(True)
        self.table.setDataSource_(self)
        self.table.setDelegate_(self)
        table_scroll = AK.NSScrollView.alloc().initWithFrame_(((20, 62), (250, 474)))
        table_scroll.setHasVerticalScroller_(True)
        table_scroll.setAutohidesScrollers_(True)
        table_scroll.setBorderType_(AK.NSBezelBorder)
        table_scroll.setDocumentView_(self.table)

        root.addSubview_(label("ID", ((294, 508), (80, 20)), 12))
        self.skill_id = _text_field(((380, 504), (444, 26)))
        root.addSubview_(label("名称", ((294, 466), (80, 20)), 12))
        self.name = _text_field(((380, 462), (444, 26)))
        root.addSubview_(label("描述", ((294, 424), (80, 20)), 12))
        self.description = _text_field(((380, 420), (444, 26)))
        root.addSubview_(label("说明", ((294, 382), (80, 20)), 12))
        self.instructions = AK.NSTextView.alloc().initWithFrame_(((0, 0), (442, 270)))
        self.instructions.setFont_(AK.NSFont.systemFontOfSize_(12))
        instructions_scroll = AK.NSScrollView.alloc().initWithFrame_(
            ((380, 112), (444, 292)))
        instructions_scroll.setHasVerticalScroller_(True)
        instructions_scroll.setAutohidesScrollers_(True)
        instructions_scroll.setBorderType_(AK.NSBezelBorder)
        instructions_scroll.setDocumentView_(self.instructions)

        self.enabled = AK.NSButton.alloc().initWithFrame_(((380, 74), (150, 24)))
        self.enabled.setButtonType_(AK.NSSwitchButton)
        self.enabled.setTitle_("启用此 Skill")
        self.enabled.setTarget_(self)
        self.enabled.setAction_("toggleEnabled:")

        self.new_button = _button(
            "新建", ((20, 20), (78, 30)), self, "newSkill:",
            AK.NSColor.systemBlueColor())
        self.refresh_button = _button(
            "刷新", ((106, 20), (78, 30)), self, "refresh:",
            AK.NSColor.systemBlueColor())
        self.save_button = _button(
            "保存", ((650, 62), (82, 32)), self, "save:",
            AK.NSColor.systemBlueColor())
        self.delete_button = _button(
            "删除…", ((742, 62), (82, 32)), self, "delete:",
            AK.NSColor.systemRedColor())
        self.status = label("", ((294, 20), (530, 28)), 11,
                            AK.NSColor.secondaryLabelColor())

        for view in (table_scroll, self.skill_id, self.name, self.description,
                     instructions_scroll, self.enabled, self.new_button,
                     self.refresh_button, self.save_button,
                     self.delete_button, self.status):
            root.addSubview_(view)
        self._set_editor_enabled(False)
        return self

    def show(self):
        self.window.makeKeyAndOrderFront_(None)
        AK.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.refresh_(None)

    def numberOfRowsInTableView_(self, _table):
        return len(self.items)

    def tableView_objectValueForTableColumn_row_(self, _table, _column, row):
        item = self.items[row]
        source = "内置" if item.get("source") == "builtin" else "用户"
        state = "已启用" if item.get("enabled") else "已停用"
        return f"{item.get('name') or item.get('skill_id')}\n{source} · {state}"

    def tableViewSelectionDidChange_(self, _notification):
        row = self.table.selectedRow()
        if 0 <= row < len(self.items):
            self._select(self.items[row])

    def refresh_(self, _sender):
        self._request("list", self.backend.application.list_skills())

    def newSkill_(self, _sender):
        self.selected = None
        self.table.deselectAll_(None)
        for field in (self.skill_id, self.name, self.description):
            field.setStringValue_("")
        self.instructions.setString_("")
        self.enabled.setState_(AK.NSControlStateValueOn)
        self._set_editor_enabled(True)
        self.skill_id.setEnabled_(True)
        self.delete_button.setEnabled_(False)
        self.status.setStringValue_("填写后保存到用户 Skill 目录")
        self.window.makeFirstResponder_(self.skill_id)

    def save_(self, _sender):
        if self.saving:
            return
        params = {
            "skill_id": self.skill_id.stringValue().strip(),
            "name": self.name.stringValue().strip(),
            "description": self.description.stringValue().strip(),
            "instructions": self.instructions.string().strip(),
        }
        if self.selected and self.selected.get("source") == "builtin":
            return
        operation = (self.backend.application.update_skill(**params)
                     if self.selected else
                     self.backend.application.create_skill(**params))
        self._request("save", operation)

    def toggleEnabled_(self, _sender):
        if not self.selected or self.saving:
            return
        enabled = self.enabled.state() == AK.NSControlStateValueOn
        self._request("toggle", self.backend.application.set_skill_enabled(
            self.selected["skill_id"], enabled))

    def delete_(self, _sender):
        if not self.selected or self.selected.get("source") != "user":
            return
        alert = AK.NSAlert.alloc().init()
        alert.setMessageText_(f"删除 Skill“{self.selected['name']}”？")
        alert.setInformativeText_("本地 SKILL.md 将被删除，且无法撤销。")
        alert.addButtonWithTitle_("删除")
        alert.addButtonWithTitle_("取消")
        alert.setAlertStyle_(AK.NSAlertStyleWarning)
        if alert.runModal() != AK.NSAlertFirstButtonReturn:
            return
        self._request("delete", self.backend.application.delete_skill(
            self.selected["skill_id"]))

    @objc.python_method
    def _request(self, purpose, coroutine):
        self.generation += 1
        generation = self.generation
        self.saving = True
        self.status.setStringValue_("正在读取…" if purpose == "list" else "正在保存…")
        self._update_buttons()
        future = self.backend.submit(coroutine)

        def complete(done):
            try:
                result, error = done.result(), ""
            except Exception as exc:
                result, error = None, str(exc) or "操作失败"
            payload = {"generation": generation, "purpose": purpose,
                       "result": result, "error": error}
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "receiveResult:", payload, False)
        future.add_done_callback(complete)

    def receiveResult_(self, payload):
        if payload.get("generation") != self.generation:
            return
        self.saving = False
        if payload.get("error"):
            self.status.setStringValue_("操作失败：" + payload["error"])
            self._update_buttons()
            return
        if payload.get("purpose") == "list":
            self._apply_list(payload.get("result") or [])
            return
        self.status.setStringValue_("已保存，Runtime Skill 配置将在下一轮生效")
        self.refresh_(None)

    @objc.python_method
    def _apply_list(self, items):
        selected_id = self.selected.get("skill_id") if self.selected else None
        self.items = list(items)
        self.table.reloadData()
        row = next((index for index, item in enumerate(self.items)
                    if item.get("skill_id") == selected_id), 0 if self.items else -1)
        if row >= 0:
            self.table.selectRowIndexes_byExtendingSelection_(
                NSIndexSet.indexSetWithIndex_(row), False)
            self._select(self.items[row])
        else:
            self.selected = None
            self._set_editor_enabled(False)
        self.status.setStringValue_(f"共 {len(self.items)} 个 Skill")
        self._update_buttons()

    @objc.python_method
    def _select(self, item):
        self.selected = item
        self.skill_id.setStringValue_(item.get("skill_id", ""))
        self.name.setStringValue_(item.get("name", ""))
        self.description.setStringValue_(item.get("description", ""))
        self.instructions.setString_(item.get("instructions", ""))
        self.enabled.setState_(AK.NSControlStateValueOn
                               if item.get("enabled") else AK.NSControlStateValueOff)
        editable = item.get("source") == "user"
        self._set_editor_enabled(editable)
        self.skill_id.setEnabled_(False)
        self.enabled.setEnabled_(not self.saving)
        self.delete_button.setEnabled_(editable and not self.saving)
        self.status.setStringValue_(
            "内置 Skill 只读，可停用" if not editable else item.get("path", ""))

    @objc.python_method
    def _set_editor_enabled(self, enabled):
        self.skill_id.setEnabled_(enabled)
        self.name.setEditable_(enabled)
        self.description.setEditable_(enabled)
        self.instructions.setEditable_(enabled)
        self.save_button.setEnabled_(enabled and not self.saving)
        self.delete_button.setEnabled_(enabled and self.selected is not None
                                       and not self.saving)
        self.enabled.setEnabled_(self.selected is not None and not self.saving)

    @objc.python_method
    def _update_buttons(self):
        editable = bool(self.selected and self.selected.get("source") == "user")
        creating = self.selected is None and self.skill_id.isEnabled()
        self.save_button.setEnabled_((editable or creating) and not self.saving)
        self.delete_button.setEnabled_(editable and not self.saving)
        self.enabled.setEnabled_(self.selected is not None and not self.saving)
        self.new_button.setEnabled_(not self.saving)
        self.refresh_button.setEnabled_(not self.saving)
