"""原生界面验收：仅使用合成文字和本地替身，不访问麦克风或操作其他应用。"""

import asyncio
import json
import traceback
import time

import AppKit as AK
from Foundation import NSRunLoop, NSDate
from PyObjCTools import AppHelper

import boxagent.desktop as host
from boxagent.appearance.codex_pets import CodexPetsAppearance
from boxagent.config import DATA, DEFAULT_PET
from boxagent.domain import Snapshot
from boxagent.runtime import Runtime


class PreviewExecutor:
    async def run(self, goal, progress, approve):
        self.goal = goal
        self.release = asyncio.Event()
        progress("界面检查：等待本地测试完成")
        await self.release.wait()
        return {"outcome": "completed", "summary": "界面检查完成，没有操作其他应用。"}

    async def cancel(self):
        pass


def main():
    output = DATA / "ui-check"
    output.mkdir(parents=True, exist_ok=True)
    host.DATA = output
    host.LOG_DIR = output
    backend = host.Backend(lambda publish: Runtime(publish, lambda _id: PreviewExecutor(), None))
    desktop = host.Desktop.alloc().init().configure(backend, CodexPetsAppearance(DEFAULT_PET))
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
        app.stop_(None)

    def idle():
        app.setAppearance_(AK.NSAppearance.appearanceNamed_(AK.NSAppearanceNameAqua))
        desktop.updateLabels()
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
        check(backend.runtime.executor.goal == "帮我打开音乐，播放一首钢琴曲", "回车提交原样到达执行器")
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
        finish()

    AppHelper.callLater(.8, lambda: later(idle))
    AppHelper.runEventLoop()
    if result.get("error"):
        raise SystemExit(result["error"])


if __name__ == "__main__":
    main()
