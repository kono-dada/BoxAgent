"""Codex Pet Share 图集适配器；素材、帧规则、语义映射均止于此层。"""

import io
import json
import math

import AppKit as AK
from Foundation import NSData
from PIL import Image

from ..config import ROOT
from ..domain import presentation_state


MAPPING = {
    "idle": "idle", "listening": "review", "speaking": "waving", "working": "running",
    "connecting": "waiting", "approval": "waiting", "success": "jumping", "error": "failed",
}


class CodexPetsAppearance:
    size = (168.0, 182.0)

    def __init__(self, directory):
        self.directory = directory
        self.manifest = json.loads((directory / "pet.json").read_text(encoding="utf-8"))
        self.contract = json.loads((ROOT / "assets/pet/atlas-contract.json").read_text(encoding="utf-8"))
        self.version = self.manifest.get("spriteVersionNumber", 1)
        format_spec = self.contract["formats"][str(self.version)]
        atlas_path = (directory / self.manifest["spritesheetPath"]).resolve()
        if not atlas_path.is_relative_to(directory.resolve()):
            raise ValueError("角色图片必须位于角色目录内")
        with Image.open(atlas_path) as source:
            atlas = source.convert("RGBA")
        if atlas.size != (format_spec["width"], format_spec["height"]):
            raise ValueError("角色图集尺寸不符合其版本")
        self.frames = {}
        width, height = self.contract["cellWidth"], self.contract["cellHeight"]
        for row in range(format_spec["rows"]):
            for column in range(self.contract["columns"]):
                png = io.BytesIO()
                atlas.crop((column * width, row * height, (column + 1) * width, (row + 1) * height)).save(png, format="PNG")
                self.frames[row, column] = AK.NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(png.getvalue(), len(png.getvalue())))
        self.view = AK.NSImageView.alloc().initWithFrame_(((0, 0), self.size))
        self.view.setImageScaling_(AK.NSImageScaleProportionallyUpOrDown)
        self.state = None
        self.started = 0
        self.frame_key = None
        self.last_pointer = None
        self.pointer_moved_at = 0

    def present(self, snapshot, now, pointer=None):
        if pointer and (self.last_pointer is None or math.dist(pointer, self.last_pointer) > 2):
            self.last_pointer, self.pointer_moved_at = pointer, now
        state = presentation_state(snapshot)
        if state != self.state:
            self.state, self.started = state, now
        animation = self.contract["animations"][MAPPING[state]]
        frame = int((now - self.started) * animation["fps"])
        if animation["loop"]:
            frame %= animation["frames"]
            key = (animation["row"], frame)
        elif frame < animation["frames"]:
            key = (animation["row"], frame)
        else:
            key = (0, int((now - self.started) * 6) % 6)
        if state == "idle" and pointer and self.version >= 2 and now - self.pointer_moved_at < 1.2:
            dx, dy = pointer
            if math.hypot(dx, dy) > 35:
                direction = round(math.degrees(math.atan2(dx, dy)) / 22.5) % 16
                spec = self.contract["gaze"]["directions"][direction]
                key = (spec["row"], spec["column"])
        if key != self.frame_key:
            self.frame_key = key
            self.view.setImage_(self.frames[key])
