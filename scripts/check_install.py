"""离线检查本机安装，不启动桌宠、云端会话或电脑操作。"""

import importlib.util
import platform
import subprocess
import sys

from boxagent.bootstrap.settings import load_settings
from boxagent.infrastructure.runtimes.codex.app_server import resolve_codex_runtime
from boxagent.infrastructure.runtimes.codex.computer_use import resolve_computer_use_client


def main():
    settings = load_settings()
    failures = []

    def check(name, condition, detail=""):
        print(f"{'通过' if condition else '缺失'}：{name}" + (f"（{detail}）" if detail else ""))
        if not condition:
            failures.append(name)

    check("Apple Silicon macOS", platform.system() == "Darwin" and platform.machine() == "arm64")
    check("宿主 Python 3.12.9", sys.version_info[:3] == (3, 12, 9))
    for module in ("AppKit", "Quartz", "pyaudio", "websockets", "PIL"):
        check(f"宿主依赖 {module}", importlib.util.find_spec(module) is not None)

    for name, executable, lock in (("视觉环境", settings.perception_python, "vision"),
                                   ("记忆环境", settings.jev_mem_python, "jev-mem")):
        check(name, executable.is_file(), str(executable))
        if executable.is_file():
            result = subprocess.run(["uv", "pip", "check", "--python", str(executable)],
                                    capture_output=True, text=True)
            check(f"{name}依赖一致性", result.returncode == 0)
            result = subprocess.run(["uv", "pip", "freeze", "--python", str(executable)],
                                    capture_output=True, text=True)
            expected = {line for line in (settings.root / f"scripts/requirements/{lock}.lock.txt")
                        .read_text().splitlines() if line and not line.startswith("#")}
            check(f"{name}锁定版本", result.returncode == 0 and set(result.stdout.splitlines()) == expected)
    for framework in ("AoqClientSdk.framework", "PluginOpus.framework"):
        check(framework, (settings.aoq_sdk_dir / framework).is_dir())
    check("3D 播放器", (settings.root / "assets/vrm/viewer.js").is_file())
    panel = settings.root / "web/panel"
    check("Electron 桌面构建", all((panel / "dist" / page).is_file() for page in ("index.html", "companion.html", "pet.html")))
    check("伙伴控制台 Electron", (panel / "node_modules/electron/dist/Electron.app/Contents/MacOS/Electron").is_file())
    check("默认 VRM 模型", (settings.default_pet / "model.vrm").stat().st_size > 1024
          if (settings.default_pet / "model.vrm").is_file() else False)
    manifest = settings.root / "scripts/model-manifests/qwen3.5-0.8b-mlx.manifest.tsv"
    if manifest.is_file():
        import hashlib
        model_valid = True
        for line in manifest.read_text().splitlines():
            filename, expected = line.split("\t")
            path = settings.perception_model / filename
            if not path.is_file():
                model_valid = False
                break
            with path.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                    model_valid = False
                    break
        check("视觉模型文件及摘要", model_valid)
    else:
        check("视觉模型校验清单", False)
    for name, resolver in (("Codex 与工具宿主", lambda: resolve_codex_runtime(settings.root)),
                           ("Computer Use 组件", resolve_computer_use_client)):
        try:
            path = resolver()
            result = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(path)],
                                    capture_output=True, text=True)
            check(name, result.returncode == 0, str(path))
            if name == "Codex 与工具宿主":
                companion = path.with_name("codex-code-mode-host")
                result = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(companion)],
                                        capture_output=True, text=True)
                check("Codex 工具宿主签名", result.returncode == 0)
        except (RuntimeError, OSError) as exc:
            check(name, False, str(exc))
    for name, value in (("DashScope Key", settings.qwen_api_key),
                        ("百炼 Workspace", settings.qwen_workspace_id),
                        ("DeepSeek Key", settings.deepseek_api_key),
                        (f"决策模型 Key ({settings.decision_provider})", settings.decision_api_key)):
        check(name, bool(value) and not value.startswith("请在"))
    print("安装检查通过；此检查不证明云端连通性、系统权限或真实界面行为。"
          if not failures else "请按 docs/setup.md 补齐以上项目后重新检查。")
    return 1 if failures else 0
