"""Native, local-only dashboard for sanitized memory graph snapshots."""

import math

import AppKit as AK
import objc
from Foundation import NSAttributedString, NSIndexSet, NSObject, NSString

NODE_COLORS = {
    "EVENT": (.29, .46, .88),
    "EPISODE": (.58, .35, .82),
    "NARRATIVE": (.85, .48, .24),
    "ENTITY": (.21, .62, .48),
    "SESSION": (.34, .58, .69),
}


def _label(text, frame, size=12, color=None):
    view = AK.NSTextField.labelWithString_(text)
    view.setFrame_(frame)
    view.setFont_(AK.NSFont.systemFontOfSize_(size))
    view.setTextColor_(color or AK.NSColor.labelColor())
    view.setLineBreakMode_(AK.NSLineBreakByTruncatingTail)
    return view


def _accent_button(title, frame, target, action, color):
    view = AK.NSButton.buttonWithTitle_target_action_(title, target, action)
    view.setFrame_(frame)
    view.setBezelStyle_(AK.NSBezelStyleRounded)
    view.setBezelColor_(color)
    view.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(title, {
        AK.NSFontAttributeName: AK.NSFont.systemFontOfSize_weight_(12, AK.NSFontWeightMedium),
        AK.NSForegroundColorAttributeName: AK.NSColor.whiteColor(),
    }))
    return view


class MemoryGraphView(AK.NSView):
    snapshot = objc.ivar()

    def initWithFrame_(self, frame):
        self = objc.super(MemoryGraphView, self).initWithFrame_(frame)
        if self is not None:
            self.snapshot = {"nodes": [], "edges": [], "selected_id": None}
        return self

    def setSnapshot_(self, snapshot):
        self.snapshot = snapshot or {"nodes": [], "edges": [], "selected_id": None}
        self.setNeedsDisplay_(True)

    def drawRect_(self, _rect):
        bounds = self.bounds()
        AK.NSColor.controlBackgroundColor().setFill()
        AK.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 12, 12).fill()

        nodes = list((self.snapshot or {}).get("nodes", []))[:18]
        if not nodes:
            NSString.stringWithString_("暂无可显示的关系").drawInRect_withAttributes_(
                ((24, bounds.size.height / 2 - 10), (bounds.size.width - 48, 24)), {
                    AK.NSFontAttributeName: AK.NSFont.systemFontOfSize_(13),
                    AK.NSForegroundColorAttributeName: AK.NSColor.secondaryLabelColor(),
                })
            return

        selected_id = (self.snapshot or {}).get("selected_id") or nodes[0]["id"]
        selected = next((node for node in nodes if node["id"] == selected_id), nodes[0])
        neighbors = [node for node in nodes if node["id"] != selected["id"]]
        cx, cy = bounds.size.width / 2, bounds.size.height / 2
        orbit = max(82, min(bounds.size.width, bounds.size.height) / 2 - 58)
        positions = {selected["id"]: (cx, cy)}
        for index, node in enumerate(neighbors):
            angle = math.tau * index / max(1, len(neighbors)) + math.pi / 2
            positions[node["id"]] = (cx + math.cos(angle) * orbit,
                                     cy + math.sin(angle) * orbit)

        visible = set(positions)
        for edge in (self.snapshot or {}).get("edges", []):
            if edge.get("source") not in visible or edge.get("target") not in visible:
                continue
            x1, y1 = positions[edge["source"]]
            x2, y2 = positions[edge["target"]]
            AK.NSColor.separatorColor().setStroke()
            path = AK.NSBezierPath.bezierPath()
            path.moveToPoint_((x1, y1))
            path.lineToPoint_((x2, y2))
            path.setLineWidth_(1.2)
            path.stroke()
            relation = edge.get("subtype") or edge.get("type") or ""
            if relation:
                NSString.stringWithString_(relation[:18]).drawAtPoint_withAttributes_(
                    ((x1 + x2) / 2 + 3, (y1 + y2) / 2 + 3), {
                        AK.NSFontAttributeName: AK.NSFont.systemFontOfSize_(8),
                        AK.NSForegroundColorAttributeName: AK.NSColor.tertiaryLabelColor(),
                    })

        for node in nodes:
            x, y = positions[node["id"]]
            is_selected = node["id"] == selected["id"]
            diameter = 62 if is_selected else 44
            red, green, blue = NODE_COLORS.get(node.get("type"), (.48, .5, .55))
            AK.NSColor.colorWithSRGBRed_green_blue_alpha_(red, green, blue, 1).setFill()
            circle = AK.NSBezierPath.bezierPathWithOvalInRect_(
                ((x - diameter / 2, y - diameter / 2), (diameter, diameter)))
            circle.fill()
            if is_selected:
                AK.NSColor.controlAccentColor().setStroke()
                circle.setLineWidth_(3)
                circle.stroke()
            content = (node.get("content") or node.get("type") or "记忆").replace("\n", " ")
            text = content[:16] + ("…" if len(content) > 16 else "")
            NSString.stringWithString_(text).drawInRect_withAttributes_(
                ((x - 58, y - diameter / 2 - 25), (116, 18)), {
                    AK.NSFontAttributeName: AK.NSFont.systemFontOfSize_(9),
                    AK.NSForegroundColorAttributeName: AK.NSColor.labelColor(),
                })


class MemoryDashboardWindow(NSObject):
    """Three-pane browser; all reads and deletes go through the application boundary."""

    @objc.python_method
    def configure(self, owner):
        self.owner = owner
        self.backend = owner.backend
        self.items = []
        self.selected_node = None
        self.generation = 0
        self.updating_selection = False

        style = (AK.NSWindowStyleMaskTitled | AK.NSWindowStyleMaskClosable
                 | AK.NSWindowStyleMaskMiniaturizable)
        self.window = AK.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (980, 640)), style, AK.NSBackingStoreBuffered, False)
        self.window.setTitle_("BoxAgent 记忆看板")
        self.window.setReleasedWhenClosed_(False)
        self.window.center()
        content = self.window.contentView()

        self.search = AK.NSSearchField.alloc().initWithFrame_(((18, 594), (350, 30)))
        self.search.setPlaceholderString_("搜索记忆内容、类型、来源或 ID")
        self.search.setTarget_(self)
        self.search.setAction_("search:")
        refresh = _accent_button("刷新", ((378, 594), (72, 30)), self, "refresh:",
                                 AK.NSColor.controlAccentColor())
        self.stats = _label("", ((466, 598), (496, 22)), 12, AK.NSColor.secondaryLabelColor())

        content.addSubview_(_label("记忆时间线", ((18, 564), (270, 22)), 13))
        self.table = AK.NSTableView.alloc().initWithFrame_(((0, 0), (270, 500)))
        column = AK.NSTableColumn.alloc().initWithIdentifier_("memory")
        column.setTitle_("记忆")
        column.setWidth_(268)
        self.table.addTableColumn_(column)
        self.table.setHeaderView_(None)
        self.table.setRowHeight_(44)
        self.table.setUsesAlternatingRowBackgroundColors_(True)
        self.table.setDataSource_(self)
        self.table.setDelegate_(self)
        table_scroll = AK.NSScrollView.alloc().initWithFrame_(((18, 56), (270, 504)))
        table_scroll.setHasVerticalScroller_(True)
        table_scroll.setAutohidesScrollers_(True)
        table_scroll.setBorderType_(AK.NSBezelBorder)
        table_scroll.setDocumentView_(self.table)

        content.addSubview_(_label("一跳关系图", ((306, 564), (382, 22)), 13))
        self.graph = MemoryGraphView.alloc().initWithFrame_(((306, 56), (382, 504)))

        content.addSubview_(_label("记忆详情", ((706, 564), (256, 22)), 13))
        self.detail = AK.NSTextView.alloc().initWithFrame_(((0, 0), (256, 396)))
        self.detail.setEditable_(False)
        self.detail.setSelectable_(True)
        self.detail.setDrawsBackground_(False)
        self.detail.setFont_(AK.NSFont.systemFontOfSize_(12))
        detail_scroll = AK.NSScrollView.alloc().initWithFrame_(((706, 148), (256, 412)))
        detail_scroll.setHasVerticalScroller_(True)
        detail_scroll.setAutohidesScrollers_(True)
        detail_scroll.setBorderType_(AK.NSBezelBorder)
        detail_scroll.setDocumentView_(self.detail)
        self.delete_button = _accent_button(
            "删除…", ((870, 62), (92, 32)), self, "deleteSelected:", AK.NSColor.systemRedColor())
        self.delete_button.setEnabled_(False)
        self.approve_button = _accent_button(
            "批准", ((706, 104), (76, 32)), self, "approveSelected:",
            AK.NSColor.systemGreenColor())
        self.reject_button = _accent_button(
            "拒绝", ((790, 104), (76, 32)), self, "rejectSelected:",
            AK.NSColor.systemOrangeColor())
        self.retry_button = _accent_button(
            "重试索引", ((874, 104), (88, 32)), self, "retrySelected:",
            AK.NSColor.controlAccentColor())
        for button in (self.approve_button, self.reject_button, self.retry_button):
            button.setEnabled_(False)

        self.status = _label("", ((18, 18), (944, 24)), 11, AK.NSColor.secondaryLabelColor())
        for view in (self.search, refresh, self.stats, table_scroll, self.graph,
                     detail_scroll, self.approve_button, self.reject_button,
                     self.retry_button, self.delete_button, self.status):
            content.addSubview_(view)
        return self

    def show(self):
        self.window.makeKeyAndOrderFront_(None)
        AK.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.refresh_(None)

    def numberOfRowsInTableView_(self, _table):
        return len(self.items)

    def tableView_objectValueForTableColumn_row_(self, _table, _column, row):
        node = self.items[row]
        content = (node.get("content") or "（无文本）").replace("\n", " ")
        stamp = (node.get("timestamp") or "")[:16].replace("T", " ")
        return f"{node.get('type', 'UNKNOWN')}  {stamp}\n{content}"

    def tableViewSelectionDidChange_(self, _notification):
        if self.updating_selection:
            return
        row = self.table.selectedRow()
        if 0 <= row < len(self.items):
            self._select(self.items[row])

    def search_(self, _sender):
        self.refresh_(None)

    def refresh_(self, _sender):
        self._load("list", query=self.search.stringValue().strip(), selected_id=None,
                   node_limit=100, edge_limit=200)

    @objc.python_method
    def _load(self, purpose, **arguments):
        self.generation += 1
        generation = self.generation
        self.status.setStringValue_("正在读取本地记忆图…")
        future = self.backend.submit(self.backend.application.memory_snapshot(**arguments))

        def complete(done):
            try:
                payload = {"purpose": purpose, "generation": generation,
                           "snapshot": done.result(), "error": ""}
            except Exception as exc:
                payload = {"purpose": purpose, "generation": generation,
                           "snapshot": {}, "error": str(exc) or "读取记忆失败"}
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "receiveResult:", payload, False)
        future.add_done_callback(complete)

    def receiveResult_(self, payload):
        if payload.get("generation") != self.generation:
            return
        error = payload.get("error")
        if error:
            self.status.setStringValue_("读取失败：" + error)
            return
        snapshot = payload.get("snapshot") or {}
        if payload.get("purpose") == "graph":
            self.graph.setSnapshot_(snapshot)
            self.status.setStringValue_("已显示选中记忆的一跳关系")
            return

        self.items = list(snapshot.get("nodes", []))
        self.table.reloadData()
        statistics = snapshot.get("statistics", {})
        suffix = " · 返回结果已截断" if snapshot.get("truncated") else ""
        self.stats.setStringValue_(
            f"{statistics.get('node_count', 0)} 个节点 · {statistics.get('edge_count', 0)} 条关系"
            f" · 匹配 {statistics.get('matched_count', len(self.items))}{suffix}")
        if not self.items:
            self.selected_node = None
            self.detail.setString_("没有匹配的记忆。\n\n只有用户明确要求记住的信息才会进入长期记忆。")
            self.delete_button.setEnabled_(False)
            self.approve_button.setEnabled_(False)
            self.reject_button.setEnabled_(False)
            self.retry_button.setEnabled_(False)
            self.graph.setSnapshot_(snapshot)
            self.status.setStringValue_("暂无记忆" if not self.search.stringValue() else "没有搜索结果")
            return
        self.updating_selection = True
        self.table.selectRowIndexes_byExtendingSelection_(NSIndexSet.indexSetWithIndex_(0), False)
        self.updating_selection = False
        self._select(self.items[0])

    @objc.python_method
    def _select(self, node):
        self.selected_node = node
        status = node.get("status", "")
        self.delete_button.setEnabled_(status != "index_only")
        self.approve_button.setEnabled_(status == "pending_review")
        self.reject_button.setEnabled_(status == "pending_review")
        self.retry_button.setEnabled_(
            status == "index_failed" or node.get("index_state") == "pending")
        self.detail.setString_(
            f"类型\n{node.get('type') or 'UNKNOWN'}\n\n"
            f"状态\n{status or 'unknown'}\n\n"
            f"修订\n{node.get('revision') or '-'}\n\n"
            f"语义槽\n{node.get('canonical_slot') or '-'}\n\n"
            f"索引\n{node.get('index_state') or '-'}"
            f"（尝试 {node.get('index_attempts') or 0} 次）\n\n"
            f"索引清理\n{node.get('index_cleanup_state') or '-'}\n\n"
            f"时间\n{node.get('timestamp') or '未记录'}\n\n"
            f"来源\n{node.get('source') or '未记录'}\n\n"
            f"内容\n{node.get('content') or '（无文本）'}\n\n"
            f"精确 ID\n{node.get('id')}")
        self._load("graph", selected_id=node["id"], node_limit=40, edge_limit=120)

    def deleteSelected_(self, _sender):
        if not self.selected_node:
            return
        memory_id = self.selected_node["id"]
        alert = AK.NSAlert.alloc().init()
        alert.setMessageText_("删除这条长期记忆？")
        alert.setInformativeText_("这会按精确 ID 同时删除图节点、向量和关键词索引，且无法撤销。")
        alert.addButtonWithTitle_("删除")
        alert.addButtonWithTitle_("取消")
        alert.setAlertStyle_(AK.NSAlertStyleWarning)
        if alert.runModal() != AK.NSAlertFirstButtonReturn:
            return
        self.delete_button.setEnabled_(False)
        self.status.setStringValue_("正在删除记忆…")
        self.generation += 1
        generation = self.generation
        future = self.backend.submit(self.backend.application.delete_memory_node(memory_id))

        def complete(done):
            try:
                result = done.result()
                error = "" if result.get("status") == "succeeded" else "没有找到这条记忆"
            except Exception as exc:
                error = str(exc) or "删除失败"
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "receiveDeleteResult:", {"generation": generation, "error": error}, False)
        future.add_done_callback(complete)

    def approveSelected_(self, _sender):
        self._mutate_selected("approve_memory", "正在批准并写入索引…")

    def rejectSelected_(self, _sender):
        self._mutate_selected("reject_memory", "正在拒绝候选…")

    def retrySelected_(self, _sender):
        self._mutate_selected("retry_memory_index", "正在重试索引…")

    @objc.python_method
    def _mutate_selected(self, method, status_text):
        if not self.selected_node:
            return
        self.status.setStringValue_(status_text)
        self.generation += 1
        generation = self.generation
        operation = getattr(self.backend.application, method)
        future = self.backend.submit(operation(self.selected_node["id"]))

        def complete(done):
            try:
                result, error = done.result(), ""
                if result.get("status") not in {"succeeded", "index_failed"}:
                    error = result.get("message") or "操作未完成"
            except Exception as exc:
                error = str(exc) or "操作失败"
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "receiveMutationResult:",
                {"generation": generation, "error": error}, False)
        future.add_done_callback(complete)

    def receiveMutationResult_(self, payload):
        if payload.get("generation") != self.generation:
            return
        if payload.get("error"):
            self.status.setStringValue_("操作失败：" + payload["error"])
            return
        self.status.setStringValue_("操作已完成")
        self.refresh_(None)

    def receiveDeleteResult_(self, payload):
        if payload.get("generation") != self.generation:
            return
        if payload.get("error"):
            self.status.setStringValue_("删除失败：" + payload["error"])
            self.delete_button.setEnabled_(True)
            return
        self.status.setStringValue_("记忆已删除")
        self.refresh_(None)
