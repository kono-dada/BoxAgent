"""Thin macOS process entry; concrete implementations live in bootstrap.desktop."""

import argparse
import signal
import fcntl
import math
from pathlib import Path

from boxagent.bootstrap.settings import load_settings


def main():
    app_settings = load_settings()
    parser = argparse.ArgumentParser(description="BoxAgent 桌宠")
    parser.add_argument("--pet", type=Path, help="本次启动指定本地形象目录；默认恢复上次选择")
    parser.add_argument("--log-dir", type=Path, default=app_settings.log_dir,
                        help="诊断日志目录，默认 <data-dir>/logs；"
                             "任务运行记录始终保存在 <data-dir>/runs")
    parser.add_argument("--require-approval", action="store_true",
                        help="电脑操作需要手动确认；默认自动允许（不影响 macOS 系统权限）")
    parser.add_argument("--task-provider", choices=("codex", "deepseek"), default=app_settings.task_provider,
                        help="桌面任务规划模型供应商，默认读取 BOXAGENT_TASK_PROVIDER")
    parser.add_argument("--task-model", help="覆盖当前任务 Provider 的模型名")
    parser.add_argument("--context-interval", type=float, default=0,
                        help="本地前台窗口摘要周期，单位秒，默认 0（启动时关闭）；正数表示启动时开启")
    parser.add_argument("--context-size", type=int, default=960,
                        help="送入本地模型的截图最长边像素，默认 960")
    args = parser.parse_args()
    if not math.isfinite(args.context_interval) or args.context_interval < 0:
        parser.error("--context-interval 必须是非负有限秒数")
    if not 320 <= args.context_size <= 1920:
        parser.error("--context-size 必须在 320 至 1920 之间")
    app_settings = app_settings.with_log_dir(args.log_dir)
    app_settings.log_dir.mkdir(parents=True, exist_ok=True)
    print(f"诊断日志：{app_settings.log_dir}", flush=True)
    app_settings.data_dir.mkdir(parents=True, exist_ok=True)
    instance_lock = (app_settings.data_dir / "instance.lock").open("w")
    try:
        fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("BoxAgent 已在运行，可从菜单栏或 ⌃⌥空格唤起。", flush=True)
        return
    import AppKit as AK
    from PyObjCTools import AppHelper
    from boxagent.bootstrap.desktop import create_desktop_host

    app = AK.NSApplication.sharedApplication()
    app.setActivationPolicy_(AK.NSApplicationActivationPolicyAccessory)
    _, delegate = create_desktop_host(
        pet_directory=args.pet.expanduser().resolve() if args.pet else None,
        task_provider=args.task_provider, task_model=args.task_model,
        auto_approve=not args.require_approval,
        context_interval=args.context_interval, context_size=args.context_size,
        app_settings=app_settings)
    app.setDelegate_(delegate)
    signal.signal(signal.SIGTERM, lambda *_: AppHelper.callAfter(delegate.quit_, None))
    signal.signal(signal.SIGINT, lambda *_: AppHelper.callAfter(delegate.quit_, None))
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
