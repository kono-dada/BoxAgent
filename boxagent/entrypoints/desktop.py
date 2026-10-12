"""Launch Electron desktop; concrete implementations live in bootstrap."""

import argparse
import math
from pathlib import Path

from boxagent.bootstrap.settings import load_settings


def main() -> None:
    app_settings = load_settings()
    parser = argparse.ArgumentParser(description="BoxAgent 桌宠")
    parser.add_argument("--pet", type=Path, help="本次启动指定本地形象目录；默认恢复上次选择")
    parser.add_argument("--log-dir", type=Path, default=app_settings.log_dir,
                        help="诊断日志目录，默认 <data-dir>/logs；"
                             "任务记录保存在 <data-dir>/sessions/<id>/tasks；未迁移/回滚时用 runs")
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
    print(f"诊断日志：{app_settings.log_dir}", flush=True)
    from boxagent.bootstrap.electron_launch import run_electron
    arguments = ["--log-dir", str(app_settings.log_dir), "--task-provider", args.task_provider,
                 "--context-interval", str(args.context_interval), "--context-size", str(args.context_size)]
    if args.pet:
        arguments.extend(("--pet", str(args.pet.expanduser().resolve())))
    if args.task_model:
        arguments.extend(("--task-model", args.task_model))
    if args.require_approval:
        arguments.append("--require-approval")
    raise SystemExit(run_electron(app_settings, arguments))


if __name__ == "__main__":
    main()
