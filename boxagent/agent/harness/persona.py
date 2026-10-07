"""Load the user-customizable BoxAgent persona from a SOUL.md file."""

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Persona:
    content: str
    source: Path
    name: str = "伙伴"


def persona_name(content: str) -> str:
    for line in content.splitlines():
        match = re.match(r"^\s*(?:名字|name)\s*[:：]\s*(.+?)\s*$", line,
                         flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip().strip("#*_` ")
            if value:
                return value[:24]
    return "伙伴"


def update_persona_name(content: str, name: str) -> str:
    """Keep SOUL.md editable while making the display name deterministic."""
    name = " ".join(name.strip().split())[:24]
    if not name:
        raise ValueError("角色名称不能为空")
    line = f"名字：{name}"
    pattern = re.compile(
        r"^[ \t]*(?:名字|name)[ \t]*[:：][ \t]*.*$",
        flags=re.IGNORECASE | re.MULTILINE)
    content = content.strip()
    if pattern.search(content):
        return pattern.sub(line, content, count=1).strip() + "\n"
    return f"{line}\n\n{content}\n" if content else f"{line}\n"


def load_persona(path: Path) -> Persona:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise RuntimeError(f"人格文件不存在：{source}")
    content = source.read_text(encoding="utf-8").strip()
    if not content:
        raise RuntimeError(f"人格文件为空：{source}")
    return Persona(content=content, source=source, name=persona_name(content))
