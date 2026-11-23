# Subset of the PyObjC bridge used by the native adapters.
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, Protocol

from Foundation import NSBundle

_C_OUT: bytes

class _SelectorDecorator(Protocol):
    def __call__[**P, R](self, function: Callable[P, R], /) -> Callable[P, R]: ...

def typedSelector(signature: bytes) -> _SelectorDecorator: ...
def loadBundle(
    module_name: str,
    module_globals: dict[str, Any],
    bundle_path: str | None = None,
    bundle_identifier: str | None = None,
    scan_classes: bool = True,
) -> NSBundle: ...
# The resolved class and its API depend on the framework loaded at runtime.
def lookUpClass(class_name: str) -> Any: ...
def registerMetaDataForSelector(
    classObject: bytes, selector: bytes, metadata: Mapping[str, object]
) -> None: ...

class autorelease_pool:
    def __enter__(self) -> None: ...
    def __exit__(
        self, exc_type: type[BaseException] | None, value: BaseException | None,
        tp: TracebackType | None,
    ) -> None: ...
