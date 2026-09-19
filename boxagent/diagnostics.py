"""任务诊断保存在本机忽略目录；不记录凭据或图片的 base64。"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path


def redact(value):
    if isinstance(value, dict):
        return {key: ("[已隐藏]" if re.search(r"authorization|api.?key|token|secret|password", key, re.I)
                      else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", "[图片另存]", value)
        value = re.sub(r"(?i)Bearer\s+\S+", "Bearer [已隐藏]", value)
        return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[已隐藏]", value)
    return value


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(redact(value), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class TaskFailure(RuntimeError):
    def __init__(self, code, message, detail=""):
        super().__init__(message)
        self.code, self.detail = code, detail


def timestamp():
    return datetime.now(timezone.utc).isoformat()
