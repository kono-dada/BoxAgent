"""Load the user-customizable BoxAgent persona from a SOUL.md file."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Persona:
    content: str
    source: Path


def load_persona(path: Path) -> Persona:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise RuntimeError(f"人格文件不存在：{source}")
    content = source.read_text(encoding="utf-8").strip()
    if not content:
        raise RuntimeError(f"人格文件为空：{source}")
    return Persona(content=content, source=source)
