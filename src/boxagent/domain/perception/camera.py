"""Bounded, ephemeral camera frames and the conversation port (no gesture rules)."""

import base64
import binascii
import math
from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

MAX_IMAGE_BASE64 = 256_000


@dataclass(frozen=True)
class CameraFrame:
    sequence: int
    captured_at: float
    image: str = field(repr=False)

    @classmethod
    def parse(cls, payload: Mapping[str, object]) -> "CameraFrame":
        sequence, captured_at, image = (
            payload.get("sequence"), payload.get("captured_at"), payload.get("image"))
        if type(sequence) is not int or sequence < 0:
            raise ValueError("Invalid camera frame sequence")
        if not isinstance(captured_at, (int, float)) or isinstance(captured_at, bool) or not math.isfinite(captured_at):
            raise ValueError("Invalid camera frame time")
        if not isinstance(image, str) or not 4 <= len(image) <= MAX_IMAGE_BASE64:
            raise ValueError("Camera JPEG must be below 256 KB after Base64 encoding")
        try:
            raw = base64.b64decode(image, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid camera JPEG encoding") from exc
        if not raw.startswith(b"\xff\xd8\xff") or not raw.endswith(b"\xff\xd9"):
            raise ValueError("Camera frame must be JPEG")
        return cls(sequence=sequence, captured_at=float(captured_at), image=image)


@runtime_checkable
class CameraFeed(Protocol):
    async def feed_image(self, image: str) -> bool: ...


class CameraRuntime(Protocol):
    async def open_camera(self) -> CameraFeed: ...
    async def close_camera(self, feed: CameraFeed) -> None: ...
    def camera_connected(self, feed: CameraFeed) -> bool: ...
