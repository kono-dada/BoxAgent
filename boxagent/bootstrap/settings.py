"""Read environment configuration only at the composition boundary."""

import os
from dataclasses import dataclass, replace
from pathlib import Path
from datetime import datetime

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
    qwen_transport: str
    qwen_workspace_id: str
    qwen_region: str
    aoq_sdk_dir: Path
    aoq_work_dir: Path
    task_provider: str
    task_model: str
    deepseek_model: str
    deepseek_base_url: str
    deepseek_api_key: str
    jev_mem_python: Path
    jev_mem_cache: Path
    jev_mem_backend: str
    typesafe_api_key: str
    perception_model: Path
    soul_file: Path
    engine_watch: bool

    @property
    def runs_dir(self) -> Path:
        """Canonical, user-auditable task-run root independent from process logs."""
        return self.data_dir / "runs"

    def task_run_dir(self, task_id: str, *, now=None) -> Path:
        moment = now or datetime.now().astimezone()
        return (self.runs_dir / moment.strftime("%Y-%m-%d")
                / f"{moment.strftime('%H%M%S')}-{task_id}")

    def with_log_dir(self, path: Path):
        return replace(self, log_dir=Path(path).expanduser().resolve())


def read_optional_setting(name):
    if value := os.environ.get(name):
        return value
    path = ROOT / ".env.local"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def read_optional_secret(name):
    return read_optional_setting(name)


def read_secret(name, message):
    if value := read_optional_secret(name):
        return value
    raise RuntimeError(message)


def resolve_soul_file(data_dir=None) -> Path:
    if configured := os.environ.get("BOXAGENT_SOUL_FILE"):
        return Path(configured).expanduser().resolve()
    local = Path(data_dir or ROOT / ".runtime/pet") / "SOUL.md"
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


def read_renamed_setting(current, legacy, default):
    """Read the canonical setting while accepting one migration alias."""
    return os.environ.get(current, os.environ.get(legacy, default))


def resolve_jev_mem_cache(data_dir):
    configured = read_renamed_setting(
        "BOXAGENT_JEV_MEM_CACHE", "BOXAGENT_JEV_MEMORY_CACHE", "")
    if configured:
        return Path(configured).expanduser().resolve()
    current = Path(data_dir) / "memory/jev-mem"
    legacy = Path(data_dir) / "memory/jev"
    # Existing installations keep using their current Store without silently
    # splitting long-term memory across two directories. New installs use the
    # explicit Jev-Mem name.
    return legacy if legacy.exists() and not current.exists() else current


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
        default_pet=ROOT / "assets/vrm/models/zome",
        voice_model=os.environ.get(
            "BOXAGENT_VOICE_MODEL", "qwen3.8-omni-flash-realtime"),
        qwen_history_character_budget=read_positive_int(
            "BOXAGENT_QWEN_HISTORY_CHARS", 24000),
        codex_history_character_budget=read_positive_int(
            "BOXAGENT_CODEX_HISTORY_CHARS", 24000),
        qwen_checkpoint_trigger_characters=read_positive_int(
            "BOXAGENT_QWEN_CHECKPOINT_TRIGGER_CHARS", 18000),
        checkpoint_source_character_limit=read_positive_int(
            "BOXAGENT_CHECKPOINT_SOURCE_CHARS", 32000),
        qwen_api_key=read_optional_secret("DASHSCOPE_API_KEY"),
        qwen_transport=(read_optional_setting("BOXAGENT_QWEN_TRANSPORT")
                        or "auto").strip().lower(),
        qwen_workspace_id=read_optional_setting(
            "BOXAGENT_DASHSCOPE_WORKSPACE_ID").strip(),
        qwen_region=(read_optional_setting("BOXAGENT_DASHSCOPE_REGION")
                     or "cn-beijing").strip(),
        aoq_sdk_dir=Path(read_optional_setting("BOXAGENT_AOQ_SDK_DIR")
                         or ROOT / ".runtime/aoq-sdk/1.3.0/frameworks").expanduser().resolve(),
        aoq_work_dir=Path(read_optional_setting("BOXAGENT_AOQ_WORK_DIR")
                          or data / "runtimes/aoq").expanduser().resolve(),
        task_provider=os.environ.get("BOXAGENT_TASK_PROVIDER", "deepseek"),
        task_model=os.environ.get("BOXAGENT_TASK_MODEL", "gpt-5.6-luna"),
        deepseek_model=os.environ.get("BOXAGENT_DEEPSEEK_MODEL", "deepseek-flash"),
        deepseek_base_url=os.environ.get("BOXAGENT_DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_api_key=read_optional_secret("DEEPSEEK_API_KEY"),
        jev_mem_python=Path(read_renamed_setting(
            "BOXAGENT_JEV_MEM_PYTHON", "BOXAGENT_JEV_MEMORY_PYTHON",
            ROOT / ".runtime/jev-mem-venv/bin/python")),
        jev_mem_cache=resolve_jev_mem_cache(data),
        jev_mem_backend=read_renamed_setting(
            "BOXAGENT_JEV_MEM_BACKEND", "BOXAGENT_JEV_MEMORY_BACKEND", "auto"),
        typesafe_api_key=read_optional_secret("TYPESAFE_API_KEY"),
        perception_model=Path(os.environ.get(
            "BOXAGENT_PERCEPTION_MODEL", ROOT / "models/qwen3.5-0.8b-mlx")),
        soul_file=resolve_soul_file(data),
        engine_watch=read_bool("BOXAGENT_ENGINE_WATCH", default=(ROOT / ".git").exists()),
    )
