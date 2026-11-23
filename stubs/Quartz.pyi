# Core Graphics window APIs used by the native observers.
from collections.abc import Sequence
from typing import NotRequired, TypedDict

kCGNullWindowID: int
kCGWindowListExcludeDesktopElements: int
kCGWindowListOptionOnScreenOnly: int
kCGScreenSaverWindowLevelKey: int

class _WindowBounds(TypedDict):
    X: float
    Y: float
    Width: float
    Height: float

class _WindowDescription(TypedDict):
    kCGWindowBounds: _WindowBounds
    kCGWindowLayer: int
    kCGWindowNumber: int
    kCGWindowOwnerPID: int
    kCGWindowAlpha: NotRequired[float]
    kCGWindowName: NotRequired[str]

def CGWindowListCopyWindowInfo(
    options: int, relative_to_window: int, /
) -> Sequence[_WindowDescription] | None: ...
def CGShieldingWindowLevel() -> int: ...
def CGWindowLevelForKey(key: int, /) -> int: ...
