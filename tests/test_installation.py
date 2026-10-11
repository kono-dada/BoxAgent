"""验证安装配置保留虚拟环境身份，且读取本机配置不会执行命令。"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from boxagent.bootstrap import settings


class InstallationTests(unittest.TestCase):
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
