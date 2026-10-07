"""Native, local-only dashboard for sanitized memory graph snapshots."""

import json
from pathlib import Path

import AppKit as AK
import objc
from Foundation import NSAttributedString, NSIndexSet, NSObject, NSURL
from WebKit import WKWebView, WKWebViewConfiguration


GRAPH_ASSET_ROOT = Path(__file__).resolve().parents[4] / "assets/memory-graph"


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


class MemoryGraphMessageHandler(NSObject):
    graph = objc.ivar()

    def userContentController_didReceiveScriptMessage_(self, _controller, message):
        body = message.body()
        kind = body.get("type") if hasattr(body, "get") else None
        if kind == "ready":
            self.graph.web_ready = True
            self.graph.renderSnapshot()
        elif kind == "select":
            node_id = body.get("nodeId")
            if node_id and self.graph.target is not None:
                self.graph.target.graphSelectedNode_(str(node_id))


class MemoryGraphView(AK.NSView):
    snapshot = objc.ivar()
    target = objc.ivar()
    web_view = objc.ivar()
    message_handler = objc.ivar()
    web_ready = objc.ivar()

    def initWithFrame_(self, frame):
        self = objc.super(MemoryGraphView, self).initWithFrame_(frame)
        if self is not None:
            self.snapshot = {"nodes": [], "edges": [], "selected_id": None}
            self.target = None
            self.web_ready = False
            configuration = WKWebViewConfiguration.alloc().init()
            self.message_handler = MemoryGraphMessageHandler.alloc().init()
            self.message_handler.graph = self
            configuration.userContentController().addScriptMessageHandler_name_(
                self.message_handler, "boxagent")
            self.web_view = WKWebView.alloc().initWithFrame_configuration_(
                self.bounds(), configuration)
            self.web_view.setAutoresizingMask_(
                AK.NSViewWidthSizable | AK.NSViewHeightSizable)
            self.addSubview_(self.web_view)
            html = GRAPH_ASSET_ROOT / "index.html"
            self.web_view.loadFileURL_allowingReadAccessToURL_(
                NSURL.fileURLWithPath_(str(html)),
                NSURL.fileURLWithPath_isDirectory_(str(GRAPH_ASSET_ROOT), True))
        return self

    def setTarget_(self, target):
        self.target = target

    def setSnapshot_(self, snapshot):
        self.snapshot = snapshot or {"nodes": [], "edges": [], "selected_id": None}
        self.renderSnapshot()

    def resetViewport_(self, _sender):
        if self.web_ready:
            self.web_view.evaluateJavaScript_completionHandler_(
                "window.boxagentGraph && window.boxagentGraph.fit()", None)

    @objc.python_method
    def renderSnapshot(self):
        if not self.web_ready:
            return
        payload = json.dumps(self.snapshot, ensure_ascii=False, separators=(",", ":"))
        self.web_view.evaluateJavaScript_completionHandler_(
            f"window.boxagentGraph && window.boxagentGraph.setSnapshot({payload})",
            None)


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
                 | AK.NSWindowStyleMaskMiniaturizable
                 | AK.NSWindowStyleMaskResizable)
        self.window = AK.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (1180, 720)), style, AK.NSBackingStoreBuffered, False)
        self.window.setMinSize_((980, 640))
        self.window.setTitle_("BoxAgent 记忆看板")
        self.window.setReleasedWhenClosed_(False)
        self.window.center()
        content = self.window.contentView()

        self.search = AK.NSSearchField.alloc().initWithFrame_(((18, 674), (350, 30)))
        self.search.setPlaceholderString_("搜索记忆内容、类型、来源或 ID")
        self.search.setTarget_(self)
        self.search.setAction_("search:")
        refresh = _accent_button("刷新", ((378, 674), (72, 30)), self, "refresh:",
                                 AK.NSColor.controlAccentColor())
        self.stats = _label("", ((466, 678), (696, 22)), 12, AK.NSColor.secondaryLabelColor())

        content.addSubview_(_label("记忆时间线", ((18, 644), (280, 22)), 13))
        self.table = AK.NSTableView.alloc().initWithFrame_(((0, 0), (280, 580)))
        column = AK.NSTableColumn.alloc().initWithIdentifier_("memory")
        column.setTitle_("记忆")
        column.setWidth_(278)
        self.table.addTableColumn_(column)
        self.table.setHeaderView_(None)
        self.table.setRowHeight_(44)
        self.table.setUsesAlternatingRowBackgroundColors_(True)
        self.table.setDataSource_(self)
        self.table.setDelegate_(self)
        table_scroll = AK.NSScrollView.alloc().initWithFrame_(((18, 56), (280, 584)))
        table_scroll.setHasVerticalScroller_(True)
        table_scroll.setAutohidesScrollers_(True)
        table_scroll.setBorderType_(AK.NSBezelBorder)
        table_scroll.setDocumentView_(self.table)

        content.addSubview_(_label("记忆关系图", ((316, 644), (160, 22)), 13))
        show_all = AK.NSButton.buttonWithTitle_target_action_("全图", self, "showAllGraph:")
        show_all.setFrame_(((728, 640), (66, 26)))
        fit = AK.NSButton.buttonWithTitle_target_action_("适配", self, "fitGraph:")
        fit.setFrame_(((798, 640), (66, 26)))
        self.graph = MemoryGraphView.alloc().initWithFrame_(((316, 56), (548, 580)))
        self.graph.setTarget_(self)
        legend = _label(
            "滚轮缩放 · 拖动画布/节点 · 点击查看 · 双击聚焦 · 顶部可筛选关系",
            ((316, 30), (548, 20)), 10, AK.NSColor.secondaryLabelColor())

        content.addSubview_(_label("记忆详情", ((882, 644), (280, 22)), 13))
        self.detail = AK.NSTextView.alloc().initWithFrame_(((0, 0), (280, 536)))
        self.detail.setEditable_(False)
        self.detail.setSelectable_(True)
        self.detail.setDrawsBackground_(False)
        self.detail.setFont_(AK.NSFont.systemFontOfSize_(12))
        detail_scroll = AK.NSScrollView.alloc().initWithFrame_(((882, 104), (280, 532)))
        detail_scroll.setHasVerticalScroller_(True)
        detail_scroll.setAutohidesScrollers_(True)
        detail_scroll.setBorderType_(AK.NSBezelBorder)
        detail_scroll.setDocumentView_(self.detail)
        self.delete_button = _accent_button(
            "删除…", ((1070, 62), (92, 32)), self, "deleteSelected:", AK.NSColor.systemRedColor())
        self.delete_button.setEnabled_(False)
        self.status = _label("", ((18, 8), (1144, 20)), 11, AK.NSColor.secondaryLabelColor())
        for view in (self.search, refresh, self.stats, table_scroll, show_all, fit,
                     self.graph, legend,
                     detail_scroll, self.delete_button, self.status):
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

    def showAllGraph_(self, _sender):
        self._load("graph_all", query=self.search.stringValue().strip(),
                   selected_id=None, node_limit=100, edge_limit=200)

    def fitGraph_(self, _sender):
        self.graph.resetViewport_(None)

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
        if payload.get("purpose") in {"graph", "graph_all"}:
            self.graph.setSnapshot_(snapshot)
            if self.selected_node is not None:
                self._render_detail(self.selected_node, snapshot)
            edge_count = len(snapshot.get("edges", []))
            if not edge_count:
                self.status.setStringValue_(
                    "当前 Jev-Mem 图中没有可显示的关系边；节点可浏览，但需后端先生成关系。")
            elif payload.get("purpose") == "graph_all":
                self.status.setStringValue_(f"已显示全图 · {edge_count} 条关系")
            else:
                self.status.setStringValue_(f"已显示一跳关系 · {edge_count} 条边")
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
            self.detail.setString_("没有匹配的记忆。\n\n明确记忆和通过 Jev-Mem 准入的用户消息会进入长期记忆。")
            self.delete_button.setEnabled_(False)
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
        self.delete_button.setEnabled_(True)
        self._render_detail(node)
        self._load("graph", selected_id=node["id"], node_limit=40, edge_limit=120)

    @objc.python_method
    def _render_detail(self, node, graph=None):
        metadata = node.get("metadata") or {}
        jev = metadata.get("jev_mem") or {}
        relations = []
        if graph:
            nodes = {item.get("id"): item for item in graph.get("nodes", [])}
            for edge in graph.get("edges", []):
                if edge.get("source") == node.get("id"):
                    direction, neighbor_id = "→", edge.get("target")
                elif edge.get("target") == node.get("id"):
                    direction, neighbor_id = "←", edge.get("source")
                else:
                    continue
                neighbor = nodes.get(neighbor_id) or {}
                label = str(edge.get("subtype") or edge.get("type") or "RELATED")
                content = (neighbor.get("content") or neighbor_id or "未知节点").replace("\n", " ")
                relations.append(f"{direction} {label} · {content[:38]}")
        relation_text = "\n".join(relations) if relations else "暂无关系"
        self.detail.setString_(
            f"类型\n{node.get('type') or 'UNKNOWN'}\n\n"
            f"准入分数\n{jev.get('admission_score') if jev else '-'}\n\n"
            f"记忆类型\n{jev.get('memory_type') if jev else '-'}\n\n"
            f"时间\n{node.get('timestamp') or '未记录'}\n\n"
            f"来源\n{node.get('source') or '未记录'}\n\n"
            f"内容\n{node.get('content') or '（无文本）'}\n\n"
            f"关系\n{relation_text}\n\n"
            f"精确 ID\n{node.get('id')}")

    @objc.python_method
    def graphSelectedNode_(self, node_id):
        node = next((item for item in self.items if item.get("id") == node_id), None)
        if node is None:
            return
        row = self.items.index(node)
        self.updating_selection = True
        self.table.selectRowIndexes_byExtendingSelection_(
            NSIndexSet.indexSetWithIndex_(row), False)
        self.table.scrollRowToVisible_(row)
        self.updating_selection = False
        self._select(node)

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

    def receiveDeleteResult_(self, payload):
        if payload.get("generation") != self.generation:
            return
        if payload.get("error"):
            self.status.setStringValue_("删除失败：" + payload["error"])
            self.delete_button.setEnabled_(True)
            return
        self.status.setStringValue_("记忆已删除")
        self.refresh_(None)
