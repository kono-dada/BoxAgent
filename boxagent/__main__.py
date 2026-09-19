"""唯一装配点：替换形象或模型实现时，在这里选择适配器。"""

import argparse
import signal
import fcntl
import math
from pathlib import Path

from .config import DATA, DEFAULT_PET
from . import config


def main():
    parser = argparse.ArgumentParser(description="BoxAgent 桌宠")
    parser.add_argument("--pet", type=Path, help="本次启动指定本地形象目录；默认恢复上次选择")
    parser.add_argument("--log-dir", type=Path, default=config.LOG_DIR,
                        help="诊断日志目录，默认 .runtime/pet；相对路径以当前工作目录为准")
    parser.add_argument("--require-approval", action="store_true",
                        help="电脑操作需要手动确认；默认自动允许（不影响 macOS 系统权限）")
    parser.add_argument("--context-interval", type=float, default=15,
                        help="本地前台窗口摘要周期，单位秒，默认 15；0 表示启动时关闭")
    parser.add_argument("--context-size", type=int, default=960,
                        help="送入本地模型的截图最长边像素，默认 960")
    args = parser.parse_args()
    if not math.isfinite(args.context_interval) or args.context_interval < 0:
        parser.error("--context-interval 必须是非负有限秒数")
    if not 320 <= args.context_size <= 1920:
        parser.error("--context-size 必须在 320 至 1920 之间")
    config.LOG_DIR = args.log_dir.expanduser().resolve()
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"诊断日志：{config.LOG_DIR}", flush=True)
    DATA.mkdir(parents=True, exist_ok=True)
    instance_lock = (DATA / "instance.lock").open("w")
    try:
        fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("BoxAgent 已在运行，可从菜单栏或 ⌃⌥空格唤起。", flush=True)
        return
    import AppKit as AK
    from PyObjCTools import AppHelper
    from .appearance.codex_pets import CodexPetsAppearance
    from .desktop import Backend, Desktop
    from .executor import CodexExecutor
    from .runtime import Runtime
    from .voice import QwenVoice
    from .context import WindowSummary
    from .pets.catalog import PetCatalog

    app = AK.NSApplication.sharedApplication()
    app.setActivationPolicy_(AK.NSApplicationActivationPolicyAccessory)
    catalog = PetCatalog()
    directory = args.pet.expanduser().resolve() if args.pet else catalog.current_directory()
    try:
        appearance = CodexPetsAppearance(directory)
    except (OSError, ValueError, KeyError, TypeError):
        if args.pet:
            raise
        print("上次形象无法显示，已恢复内置小鸭。", flush=True)
        appearance = CodexPetsAppearance(DEFAULT_PET)
    backend = Backend(lambda publish: Runtime(publish,
        lambda task_id: CodexExecutor(task_id, auto_approve=not args.require_approval), QwenVoice))
    backend.runtime.observer = WindowSummary(backend.runtime.emit, args.context_interval or 15,
                                             max_size=args.context_size)
    if args.context_interval:
        backend.submit(backend.runtime.toggle_context())
    delegate = Desktop.alloc().init().configure(backend, appearance, catalog)
    app.setDelegate_(delegate)
    signal.signal(signal.SIGTERM, lambda *_: AppHelper.callAfter(delegate.quit_, None))
    signal.signal(signal.SIGINT, lambda *_: AppHelper.callAfter(delegate.quit_, None))
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
