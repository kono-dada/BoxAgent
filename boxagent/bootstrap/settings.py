"""Read environment configuration only at the composition boundary."""

import os
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    log_dir: Path
    codex_home: Path
    builtin_skills_dir: Path
    user_skills_dir: Path
    default_pet: Path
    voice_model: str
    qwen_history_character_budget: int
    codex_history_character_budget: int
    qwen_checkpoint_trigger_characters: int
    checkpoint_source_character_limit: int
    qwen_api_key: str
    task_provider: str
    task_model: str
    deepseek_model: str
    deepseek_base_url: str
    deepseek_api_key: str
    jev_memory_source: Path
    jev_memory_python: Path
    jev_memory_cache: Path
    jev_memory_backend: str
    typesafe_api_key: str
    perception_model: Path
    soul_file: Path
    engine_watch: bool

    def with_log_dir(self, path: Path):
        return replace(self, log_dir=Path(path).expanduser().resolve())


def read_optional_secret(name):
    if value := os.environ.get(name):
        return value
    path = ROOT / ".env.local"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def read_secret(name, message):
    if value := read_optional_secret(name):
        return value
    raise RuntimeError(message)


def resolve_soul_file() -> Path:
    if configured := os.environ.get("BOXAGENT_SOUL_FILE"):
        return Path(configured).expanduser().resolve()
    local = ROOT / ".runtime/pet/SOUL.md"
    if local.is_file():
        return local
    return ROOT / "assets/personas/default/SOUL.md"


def read_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def read_positive_int(name, default):
    raw = os.environ.get(name)
    value = int(raw) if raw is not None else default
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


def load_settings(*, log_dir=None) -> Settings:
    data = Path(os.environ.get(
        "BOXAGENT_DATA_DIR", ROOT / ".runtime/pet")).expanduser().resolve()
    configured_log = Path(log_dir).expanduser().resolve() if log_dir else data / "logs"
    return Settings(
        root=ROOT,
        data_dir=data,
        log_dir=configured_log,
        codex_home=Path(os.environ.get(
            "BOXAGENT_CODEX_HOME", data / "runtimes/codex-home")),
        builtin_skills_dir=ROOT / "skills/builtin",
        user_skills_dir=Path(os.environ.get(
            "BOXAGENT_SKILLS_DIR", data / "skills")).expanduser().resolve(),
        default_pet=ROOT / "assets/pet/debug-duck-v2",
        voice_model=os.environ.get("BOXAGENT_VOICE_MODEL", "qwen-audio-3.0-realtime-plus"),
        qwen_history_character_budget=read_positive_int(
            "BOXAGENT_QWEN_HISTORY_CHARS", 24000),
        codex_history_character_budget=read_positive_int(
            "BOXAGENT_CODEX_HISTORY_CHARS", 24000),
        qwen_checkpoint_trigger_characters=read_positive_int(
            "BOXAGENT_QWEN_CHECKPOINT_TRIGGER_CHARS", 18000),
        checkpoint_source_character_limit=read_positive_int(
            "BOXAGENT_CHECKPOINT_SOURCE_CHARS", 32000),
        qwen_api_key=read_optional_secret("DASHSCOPE_API_KEY"),
        task_provider=os.environ.get("BOXAGENT_TASK_PROVIDER", "codex"),
        task_model=os.environ.get("BOXAGENT_TASK_MODEL", "gpt-5.6-luna"),
        deepseek_model=os.environ.get("BOXAGENT_DEEPSEEK_MODEL", "deepseek-flash"),
        deepseek_base_url=os.environ.get("BOXAGENT_DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_api_key=read_optional_secret("DEEPSEEK_API_KEY"),
        jev_memory_source=Path(os.environ.get(
            "BOXAGENT_JEV_MEMORY_SOURCE", ROOT / ".runtime/jev-mem-src")),
        jev_memory_python=Path(os.environ.get(
            "BOXAGENT_JEV_MEMORY_PYTHON", ROOT / ".runtime/jev-mem-venv/bin/python")),
        jev_memory_cache=Path(os.environ.get(
            "BOXAGENT_JEV_MEMORY_CACHE", data / "memory/jev")),
        jev_memory_backend=os.environ.get("BOXAGENT_JEV_MEMORY_BACKEND", "auto"),
        typesafe_api_key=read_optional_secret("TYPESAFE_API_KEY"),
        perception_model=Path(os.environ.get(
            "BOXAGENT_PERCEPTION_MODEL", ROOT / "models/qwen3.5-0.8b-mlx")),
        soul_file=resolve_soul_file(),
        engine_watch=read_bool("BOXAGENT_ENGINE_WATCH", default=(ROOT / ".git").exists()),
    )
