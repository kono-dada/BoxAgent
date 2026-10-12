"""验证安装配置保留虚拟环境身份，且读取本机配置不会执行命令。"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from boxagent.bootstrap import settings


class InstallationTests(unittest.TestCase):
    def test_command_reuses_global_python_and_preserves_arguments(self):
        result, calls = self._launch_fixture("global", ["--task-model", "model with spaces"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"UV_PYTHON_INSTALL_DIR": null', calls)
        self.assertIn('"--no-python-downloads"', calls)
        self.assertIn('"model with spaces"', calls)
        self.assertIn('"--locked"', calls)

    def test_command_missing_python_exits_without_run_or_download(self):
        result, calls = self._launch_fixture("missing", [])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("请先运行 ./scripts/install.sh", result.stderr)
        self.assertNotIn('"run"', calls)

    def test_command_respects_explicit_python_and_offline_check(self):
        result, calls = self._launch_fixture("explicit", ["--check-install"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('"UV_PYTHON_INSTALL_DIR": null', calls)
        self.assertIn('"--offline"', calls)
        self.assertIn('"--no-sync"', calls)

    def test_command_rejects_incompatible_explicit_python(self):
        result, calls = self._launch_fixture("incompatible", [])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("需要 Python 3.12", result.stderr)
        self.assertNotIn('"run"', calls)

    def _launch_fixture(self, mode: str, arguments: list[str]) -> tuple[subprocess.CompletedProcess[str], str]:
        """Exercise the real shell entrypoints without network or app startup."""
        import shutil
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="boxagent launcher ") as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            for name in ("run-pet.sh", "runtime-env.sh"):
                shutil.copy2(repo / "scripts" / name, root / "scripts" / name)
            shutil.copy2(repo / "启动桌宠.command", root / "启动桌宠.command")
            uv_bin = root / ".local/bin"
            uv_bin.mkdir(parents=True)
            log = root / "calls.jsonl"
            uv = uv_bin / "uv"
            uv.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ["LAUNCHER_LOG"]).open("a") as stream:
    stream.write(json.dumps({{"args": args, "UV_PYTHON_INSTALL_DIR": os.environ.get("UV_PYTHON_INSTALL_DIR")}}) + "\\n")
if args[:2] == ["python", "find"]:
    if os.environ["LAUNCHER_MODE"] == "missing": sys.exit(1)
    if os.environ["LAUNCHER_MODE"] == "global" and os.environ.get("UV_PYTHON_INSTALL_DIR") and args[-1] == "3.12": sys.exit(1)
    print(os.environ.get("LAUNCHER_PYTHON", sys.executable))
elif args[0] == "run": sys.exit(0)
else: sys.exit(2)
''')
            uv.chmod(0o755)
            # run-pet prepends HOME/.local/bin; use a fixture HOME to reach our uv.
            env = {"HOME": str(root), "PATH": f"{uv_bin}:/usr/bin:/bin",
                   "LAUNCHER_MODE": mode, "LAUNCHER_LOG": str(log)}
            if mode in ("explicit", "incompatible"):
                env["UV_PYTHON"] = sys.executable
            if mode == "incompatible":
                incompatible = root / "python"
                incompatible.write_text('#!/bin/sh\nexit 1\n')
                incompatible.chmod(0o755)
                env["LAUNCHER_PYTHON"] = str(incompatible)
            result = subprocess.run([str(root / "启动桌宠.command"), *arguments],
                                    cwd="/tmp", env=env, text=True, capture_output=True)
            return result, log.read_text() if log.exists() else result.stderr

    def test_session_layout_respects_rollback_without_original_legacy_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(settings, "ROOT", root), patch.dict(
                    os.environ, {"BOXAGENT_DATA_DIR": str(root)}, clear=True):
                configured = settings.load_settings()
            self.assertTrue(configured.uses_unified_sessions)
            (root / "sessions").mkdir()
            (root / "sessions/.migrated-v1").touch()
            (root / "sessions/.rollback-v1").touch()
            self.assertFalse(configured.uses_unified_sessions)
            (root / "sessions/.rollback-v1").unlink()
            self.assertTrue(configured.uses_unified_sessions)
            (root / "sessions/.migrated-v1").unlink()
            (root / "conversations").mkdir()
            (root / "conversations/index.json").write_text('{}')
            self.assertFalse(configured.uses_unified_sessions)

    def test_task_output_uses_legacy_after_rollback_despite_retained_state(self):
        from boxagent.bootstrap.engine import create_task_executor
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(settings, "ROOT", root), patch.dict(
                    os.environ, {"BOXAGENT_DATA_DIR": str(root)}, clear=True):
                configured = settings.load_settings()
            state = root / 'sessions/ses_one/state.json'
            state.parent.mkdir(parents=True)
            state.write_text('{}')
            registry = Mock()
            registry.runtime.return_value.provider = 'codex'
            with patch('boxagent.bootstrap.engine.TaskExecutor') as executor:
                create_task_executor('abc123abc123', app_settings=configured,
                                     registry=registry, session_id='ses_one',
                                     session_factory=Mock(), request_builder=Mock())
                self.assertEqual(executor.call_args.kwargs['output'],
                                 state.parent / 'tasks/abc123abc123')
                (root / 'sessions/.rollback-v1').touch()
                create_task_executor('abc123abc123', app_settings=configured,
                                     registry=registry, session_id='ses_one',
                                     session_factory=Mock(), request_builder=Mock())
                self.assertTrue(executor.call_args.kwargs['output'].is_relative_to(root / 'runs'))
                self.assertEqual(executor.call_args.kwargs['run_index_path'], root / 'runs/index.jsonl')

    def test_perception_python_keeps_virtual_environment_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base-python"
            base.touch()
            executable = root / "vision/bin/python"
            executable.parent.mkdir(parents=True)
            executable.symlink_to(base)
            with patch.object(settings, "ROOT", root), patch.dict(
                    os.environ, {"BOXAGENT_PERCEPTION_PYTHON": str(executable)}, clear=True):
                configured = settings.load_settings()
            self.assertEqual(configured.perception_python, executable)
            self.assertNotEqual(configured.perception_python, base)

    def test_runtime_config_is_literal_and_environment_takes_precedence(self):
        loader = Path(__file__).resolve().parents[1] / "scripts/runtime-env.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "executed"
            literal = f"$(touch {marker})"
            (root / ".env.local").write_text(
                f'UV_CACHE_DIR="{root}/from-file"\n'
                f'BOXAGENT_PERCEPTION_MODEL="{literal}"\n'
                'DEEPSEEK_API_KEY=不要导出此值\n')
            result = subprocess.run(
                ["sh", "-c", 'BOXAGENT_ROOT=$1; . "$2"; '
                 'printf "%s\\n" "$UV_CACHE_DIR" "$BOXAGENT_PERCEPTION_MODEL" '
                 '"${DEEPSEEK_API_KEY-unset}" "$UV_PYTHON_BIN_DIR"',
                 "runtime-config-test", str(root), str(loader)],
                env={"PATH": os.environ["PATH"], "UV_CACHE_DIR": str(root / "from-env")},
                capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.splitlines(),
                             [str(root / "from-env"), literal, "unset", str(root / ".runtime/python/bin")])
            self.assertFalse(marker.exists())
