"""原生界面验收：仅使用合成文字和本地替身，不访问麦克风或操作其他应用。"""

import asyncio
import json
import tempfile
import traceback
import time
from pathlib import Path

import AppKit as AK
from Foundation import NSRunLoop, NSDate, NSIndexSet
from PyObjCTools import AppHelper

import boxagent.interfaces.macos.app as host
from boxagent.interfaces.macos.pets.vrm import VrmAppearance
from boxagent.infrastructure.persistence import SkillFileRepository
from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.memory import MemoryModule
from boxagent.bootstrap.settings import load_settings
from boxagent.core.states import Snapshot
from boxagent.domain.skill import SkillService
from boxagent.domain.memory.service import MemoryService
from boxagent.interfaces.macos.inprocess_bridge import BackendBridge
from boxagent.interfaces.macos.windows.memory import MemoryDashboardWindow
from boxagent.interfaces.macos.windows.skills import SkillManagerWindow


class PreviewExecutor:
    async def run(self, goal, progress, approve):
        self.goal = goal
        self.release = asyncio.Event()
        progress("界面检查：等待本地测试完成")
        await self.release.wait()
        return {"outcome": "completed", "summary": "界面检查完成，没有操作其他应用。"}

    async def cancel(self):
        pass


class PreviewMemory:
    def __init__(self):
        self.nodes = [
            {"id": "memory-1", "type": "EVENT", "content": "用户喜欢简洁直接的回答",
             "timestamp": "2026-10-05T10:00:00", "source": "voice_explicit"},
            {"id": "memory-2", "type": "EVENT", "content": "正在开发 BoxAgent 记忆看板",
             "timestamp": "2026-10-05T10:05:00", "source": "explicit"},
            {"id": "episode-1", "type": "EPISODE", "content": "BoxAgent 产品开发",
             "timestamp": "2026-10-05T10:04:00", "source": "jev_mem"},
        ]
        self.edges = [
            {"id": "edge-1", "source": "memory-1", "target": "memory-2",
             "type": "TEMPORAL", "subtype": "PRECEDES"},
            {"id": "edge-2", "source": "memory-2", "target": "episode-1",
             "type": "SEMANTIC", "subtype": "PART_OF"},
        ]

    async def inspect(self, *, query="", selected_id=None, node_limit=100, edge_limit=200):
        if selected_id:
            edges = [edge for edge in self.edges
                     if selected_id in (edge["source"], edge["target"])]
            ids = {selected_id}
            for edge in edges:
                ids.update((edge["source"], edge["target"]))
            nodes = [node for node in self.nodes if node["id"] in ids]
        else:
            nodes = [node for node in self.nodes
                     if not query or query.casefold() in node["content"].casefold()]
            nodes.sort(key=lambda node: node.get("timestamp") or "", reverse=True)
            ids = {node["id"] for node in nodes}
            edges = [edge for edge in self.edges
                     if edge["source"] in ids and edge["target"] in ids]
        return {"nodes": nodes[:node_limit], "edges": edges[:edge_limit],
                "selected_id": selected_id, "truncated": False,
                "statistics": {"node_count": len(self.nodes), "edge_count": len(self.edges),
                               "matched_count": len(nodes),
                               "node_types": {"EVENT": 2, "EPISODE": 1},
                               "link_types": {"TEMPORAL": 1, "SEMANTIC": 1}}}

    async def forget(self, memory_ids):
        deleted = [node for node in self.nodes if node["id"] in memory_ids]
        self.nodes = [node for node in self.nodes if node["id"] not in memory_ids]
        return {"deleted": deleted, "missing": [], "memory_count": len(self.nodes)}

    async def close(self):
        pass


def main():
    app_settings = load_settings()
    output = app_settings.data_dir / "ui-check"
    output.mkdir(parents=True, exist_ok=True)
    skill_sandbox = tempfile.TemporaryDirectory(prefix="boxagent-skill-ui-")
    skill_service = SkillService(SkillFileRepository(
        builtin_root=app_settings.builtin_skills_dir,
        user_root=Path(skill_sandbox.name) / "skills"))
    backend = BackendBridge(lambda publish: BoxAgentApplication(
        publish, lambda _id: PreviewExecutor(), None,
        memory=MemoryModule(MemoryService(PreviewMemory())),
        skill_service=skill_service),
        log_dir=output)
    desktop = host.Desktop.alloc().init().configure(
        backend, VrmAppearance(app_settings.default_pet),
        data_dir=output,
        memory_dashboard_factory=lambda owner:
            MemoryDashboardWindow.alloc().init().configure(owner),
        skill_manager_factory=lambda owner:
            SkillManagerWindow.alloc().init().configure(owner))
    app = AK.NSApplication.sharedApplication()
    app.setActivationPolicy_(AK.NSApplicationActivationPolicyAccessory)
    app.setDelegate_(desktop)
    result = {"checks": [], "screenshots": []}

    def check(value, name):
        if not value:
            raise AssertionError(name)
        result["checks"].append(name)

    def capture(name):
        desktop.bubble.displayIfNeeded()
        # 等待窗口合成完成，避免截到上一个状态或窗口调整中的一帧。
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.2))
        render(desktop.bubble.contentView(), output / f"{name}.png")
        result["screenshots"].append(name)

    def render(view, path):
        # 从原生视图渲染测试图，锁屏时也能检查布局，不截个人桌面。
        bitmap = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
        view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), bitmap)
        bitmap.representationUsingType_properties_(AK.NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(str(path), True)

    def later(function):
        def guarded():
            try:
                function()
            except Exception:
                result["error"] = traceback.format_exc()
                finish()
        AppHelper.callLater(.4, guarded)

    def finish():
        (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        # terminate_ 会直接以 0 退出进程；先清理再停事件循环，让失败返回非零。
        desktop.applicationShouldTerminate_(app)
        skill_sandbox.cleanup()
        AppHelper.stopEventLoop()

    def idle():
        app.setAppearance_(AK.NSAppearance.appearanceNamed_(AK.NSAppearanceNameAqua))
        desktop.updateLabels()
        check(desktop.pet.isVisible(), "启动时显示桌宠")
        check(not desktop.bubble.isVisible() and not desktop.bubble_open,
              "启动时不打开聊天面板")
        check(not desktop.context_bubble.isVisible(), "启动时不显示屏幕总结")
        desktop.toggleBubble_(None)
        check(desktop.bubble.isVisible(), "手动打开聊天面板")
        check(desktop.bubble.frame().size.height <= 180, "空闲面板不超过 180pt")
        check(desktop.cancel_button.isHidden(), "空闲时隐藏停止按钮")
        check(not desktop.send_button.isEnabled(), "空白输入不能提交")
        capture("idle")
        desktop.bubble.makeKeyWindow()
        desktop.bubble.makeFirstResponder_(desktop.input)
        editor = desktop.input.currentEditor()
        check(editor is not None, "非激活面板支持输入焦点")
        editor.setMarkedText_selectedRange_replacementRange_("测试", (2, 0), (AK.NSNotFound, 0))
        desktop.submitText_(None)
        check(desktop.submission is None, "中文组合输入不会提前提交")
        editor.unmarkText()
        desktop.input.setStringValue_("帮我打开音乐，播放一首钢琴曲")
        desktop.controlTextDidChange_(None)
        check(desktop.send_button.isEnabled(), "有内容时启用执行按钮")
        capture("ready")
        editor.insertNewline_(None)
        later(running)

    def running():
        desktop.tick_(None)
        check(backend.application.executor.goal == "帮我打开音乐，播放一首钢琴曲",
              "回车提交原样到达执行器")
        check(desktop.input.stringValue() == "", "接受任务后清空已提交文字")
        check(not desktop.cancel_button.isHidden(), "运行时显示停止按钮")
        desktop.input.setStringValue_("保留这份下一步草稿")
        desktop.submitText_(None)
        later(busy)

    def busy():
        desktop.tick_(None)
        check(desktop.input.stringValue() == "保留这份下一步草稿", "忙碌拒绝后保留草稿")
        capture("running")
        desktop.state = Snapshot(task="awaiting_approval", approval="允许操作音乐？",
                                 user_text="帮我打开音乐，播放一首钢琴曲")
        desktop.input_feedback = ""
        desktop.updateLabels()
        capture("approval")
        desktop.state = Snapshot(task="succeeded", task_text="这是用于检查滚动的长结果。" * 90)
        desktop.updateLabels()
        check(desktop.bubble.frame().size.height <= 328, "长结果限制面板高度")
        check(desktop.transcript.frame().size.height > desktop.transcript_scroll.frame().size.height,
              "长结果完整保留并支持滚动")
        capture("long-result")
        app.setAppearance_(AK.NSAppearance.appearanceNamed_(AK.NSAppearanceNameDarkAqua))
        desktop.state = Snapshot(task="succeeded", task_text="界面检查完成，没有操作其他应用。")
        desktop.updateLabels()
        later(dark)

    def dark():
        capture("dark")
        desktop.state = Snapshot(task="blocked", task_text="电脑操作服务连接超时，未能读取目标应用。请稍后重试。",
                                 task_started_at=time.time() - 43, task_ended_at=time.time())
        desktop.updateLabels()
        check("已结束" in desktop.mode_label.stringValue(), "失败状态明确说明任务已结束")
        check(not hasattr(desktop, "logs_button"), "界面不提供日志按钮")
        capture("failure")
        desktop.close_button.performClick_(None)
        check(not desktop.bubble.isVisible(), "关闭按钮收起面板")
        desktop.showBubble()
        check(desktop.input.stringValue() == "保留这份下一步草稿", "收起再打开保留草稿")
        desktop.toggleBubble_(None)
        desktop.backend.events.put({"type": "context.updated", "state": Snapshot(
            context_enabled=True, context_text="正在编辑 Python 文件，查看前台窗口总结的实现。",
            context_app="代码编辑器", context_at=time.time()).payload()})
        desktop.tick_(None)
        check(desktop.context_bubble.isVisible(), "摘要气泡浮现")
        check(not desktop.context_bubble.canBecomeKeyWindow(), "摘要气泡不能抢焦点")
        check(desktop.context_label.stringValue() == "", "更新时从空文本开始打字")
        desktop.context_reveal_at -= .3
        desktop.tick_(None)
        check(0 < len(desktop.context_label.stringValue()) < len(desktop.state.context_text), "逐字显示中间帧")
        desktop.context_reveal_at = time.monotonic() - 2
        desktop.tick_(None)
        check(desktop.context_label.stringValue() == desktop.state.context_text, "打字结束显示完整摘要")
        desktop.context_bubble.displayIfNeeded()
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.2))
        render(desktop.context_bubble.contentView(), output / "context.png")
        check(desktop.context_menu_item.title() == "关闭屏幕总结", "菜单反映开关状态")
        long_summary = "这是较长的窗口摘要，用于检查自动换行与完整显示。" * 70 + "全文结束。"
        desktop.backend.events.put({"type": "context.updated", "state": Snapshot(
            context_enabled=True, context_text=long_summary,
            context_app="代码编辑器", context_at=time.time()).payload()})
        desktop.tick_(None)
        desktop.context_reveal_at = time.monotonic() - 2
        desktop.tick_(None)
        check(desktop.context_label.stringValue() == long_summary, "长摘要在两秒内显示全文")
        check(desktop.context_label.maximumNumberOfLines() == 0, "摘要无行数限制")
        check(desktop.context_document.frame().size.height > desktop.context_scroll.frame().size.height,
              "超高摘要支持滚动")
        desktop.context_document.scrollPoint_((0, desktop.context_document.frame().size.height))
        render(desktop.context_bubble.contentView(), output / "context-long.png")
        desktop.context_until = 0
        desktop.tick_(None)
        check(not desktop.context_bubble.isVisible(), "摘要到期自动消失")
        titles = [item.title() for item in desktop.menu.itemArray()]
        check("记忆看板…" in titles, "菜单栏提供记忆看板入口")
        check("Skill 管理…" in titles, "菜单栏提供 Skill 管理入口")
        desktop.showMemoryDashboard_(None)
        later(dashboard)

    def dashboard():
        window = desktop.memory_dashboard
        check(window.window.isVisible(), "记忆看板以独立原生窗口打开")
        check(len(window.items) == 3, "看板加载脱敏记忆列表")
        check(window.selected_node["id"] == "memory-2", "默认选择最新记忆")
        check(len(window.graph.snapshot["nodes"]) == 3, "选中节点显示一跳邻居")
        check("memory-2" in window.detail.string(), "详情展示精确记忆 ID")
        window.window.displayIfNeeded()
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.2))
        render(window.window.contentView(), output / "memory-dashboard.png")
        result["screenshots"].append("memory-dashboard")
        desktop.showSkillManager_(None)
        later(skill_manager)

    def skill_manager():
        window = desktop.skill_manager
        check(window.window.isVisible(), "Skill 管理以独立原生窗口打开")
        check(any(item["source"] == "builtin" for item in window.items),
              "Skill 管理加载内置 Skill")
        window.newSkill_(None)
        window.skill_id.setStringValue_("ui-preview")
        window.name.setStringValue_("UI Preview")
        window.description.setStringValue_("验证 Skill 编辑界面")
        window.instructions.setString_("只用于本地 UI 冒烟验证。")
        window.save_(None)
        later(skill_saved)

    def skill_saved():
        window = desktop.skill_manager
        created = next((item for item in window.items
                        if item["skill_id"] == "ui-preview"), None)
        check(created is not None, "Skill 管理可新建用户 Skill")
        check(created["instructions"] == "只用于本地 UI 冒烟验证。",
              "Skill 正文通过 UI 完整落盘和回读")
        row = next(index for index, item in enumerate(window.items)
                   if item["skill_id"] == "ui-preview")
        window.table.selectRowIndexes_byExtendingSelection_(
            NSIndexSet.indexSetWithIndex_(row), False)
        window._select(window.items[row])
        check(window.skill_id.stringValue() == "ui-preview",
              "Skill ID 在深色模式编辑器中可回读")
        check(window.name.stringValue() == "UI Preview",
              "Skill 名称在深色模式编辑器中可回读")
        check(window.save_button.attributedTitle().string() == "保存",
              "Skill 操作按钮标题可见")
        window.window.displayIfNeeded()
        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(.2))
        render(window.window.contentView(), output / "skill-manager.png")
        result["screenshots"].append("skill-manager")
        finish()

    AppHelper.callLater(.8, lambda: later(idle))
    AppHelper.runEventLoop()
    if result.get("error"):
        raise SystemExit(result["error"])


if __name__ == "__main__":
    main()
