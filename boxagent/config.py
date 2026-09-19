"""配置只在装配边界读取，不把凭据传给任务执行进程。"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".runtime/pet"
LOG_DIR = DATA
DEFAULT_PET = ROOT / "assets/pet/debug-duck-v2"
VOICE_MODEL = os.environ.get("BOXAGENT_VOICE_MODEL", "qwen-audio-3.0-realtime-plus")
TASK_MODEL = os.environ.get("BOXAGENT_TASK_MODEL", "gpt-5.6-luna")


def read_key():
    if value := os.environ.get("DASHSCOPE_API_KEY"):
        return value
    path = ROOT / ".env.local"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DASHSCOPE_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("未配置千问密钥，请先运行 zsh scripts/set-key.zsh")
