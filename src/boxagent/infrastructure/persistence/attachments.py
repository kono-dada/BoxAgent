"""Content-addressed image storage with integrity-checked references."""

import hashlib
import os
from pathlib import Path


class AttachmentStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, content: bytes, mime: str) -> dict[str, str | int]:
        digest = hashlib.sha256(content).hexdigest()
        suffix = ".png" if mime == "image/png" else ".jpg"
        path = self.root / digest[:2] / (digest + suffix)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            temporary = path.with_suffix(".tmp")
            with temporary.open("wb") as stream:
                os.chmod(temporary, 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("附件哈希校验失败")
        return {"sha256": digest, "mime": mime, "bytes": len(content), "path": str(path)}
