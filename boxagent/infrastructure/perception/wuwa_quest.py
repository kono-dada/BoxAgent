"""Read the visible, tracked quest panel using the native macOS Vision framework."""

import re
from functools import lru_cache
from io import BytesIO
from typing import Any

from PIL import Image

from boxagent.domain.proactivity.game_entry import QuestReading, TrackedQuest

DISTANCE = re.compile(r"^[0-9,]+(?:[.][0-9]+)?(?:千米|公里|米|m|km)[▲△▼▽]?$", re.IGNORECASE)
CHINESE = re.compile(r"[\u3400-\u9fff]")


def tracked_quest(lines: tuple[QuestReading, ...]) -> TrackedQuest | None:
    for distance in lines:
        if not DISTANCE.fullmatch(distance.text.replace(" ", "")):
            continue
        above = sorted((item for item in lines
                        if 0 < item.y - distance.y <= max(.24, distance.height * 4)
                        and abs(item.x - distance.x) < .15
                        and 2 <= len(CHINESE.findall(item.text)) <= 24),
                       key=lambda item: item.y)
        if len(above) < 2:
            continue
        objective, title = above[:2]
        if len(title.text) <= 40 and len(objective.text) <= 42:
            return TrackedQuest(title.text.strip(), objective.text.strip())
    return None


@lru_cache(maxsize=1)
def _vision_classes() -> tuple[Any, Any]:
    import objc
    from Foundation import NSBundle

    if not NSBundle.bundleWithPath_("/System/Library/Frameworks/Vision.framework").load():
        raise RuntimeError("Vision framework unavailable")
    objc.registerMetaDataForSelector(b"VNImageRequestHandler", b"performRequests:error:",
                                     {"arguments": {3: {"type_modifier": objc._C_OUT}}})
    return objc.lookUpClass("VNRecognizeTextRequest"), objc.lookUpClass("VNImageRequestHandler")


def read_quest(data: bytes) -> TrackedQuest | None:
    import objc
    from Foundation import NSData, NSDictionary

    with Image.open(BytesIO(data)) as source, BytesIO() as encoded:
        width, height = source.size
        crop = source.crop((0, round(height * .14), round(width * .30), round(height * .48)))
        crop.thumbnail((800, 735))
        crop.save(encoded, format="PNG")
        pixels = encoded.getvalue()

    request_class, handler_class = _vision_classes()
    with objc.autorelease_pool():
        request = request_class.alloc().init()
        request.setRevision_(3)
        request.setUsesCPUOnly_(True)
        request.setRecognitionLanguages_(["zh-Hans", "en-US"])
        handler = handler_class.alloc().initWithData_options_(
            NSData.dataWithBytes_length_(pixels, len(pixels)), NSDictionary.dictionary())
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(f"Vision task tracking OCR failed: {error}")
        records: list[QuestReading] = []
        for observation in request.results() or ():
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            bounds = observation.boundingBox()
            records.append(QuestReading(str(candidate.string()), bounds.origin.x,
                                        bounds.origin.y, bounds.size.height))
        return tracked_quest(tuple(records))
