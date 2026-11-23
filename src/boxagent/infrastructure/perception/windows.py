"""Windows foreground window access; loaded only on Windows."""

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, cast


class WindowRect(TypedDict):
    x: int
    y: int
    width: int
    height: int


class WindowInfo(TypedDict):
    id: int
    app: str
    title: NotRequired[str]
    layer: NotRequired[int]
    bounds: NotRequired[WindowRect]
    bundle_id: NotRequired[str]


class _WindowsCtypes(Protocol):
    def WinDLL(self, name: str, /, *, use_last_error: bool) -> ctypes.CDLL: ...

    def get_last_error(self) -> int: ...

    def WinError(self, code: int, /) -> OSError: ...


# ctypes publishes these APIs only on Windows.
_windows_ctypes = cast(_WindowsCtypes, ctypes)


def focused_window() -> WindowInfo | None:
    user32 = _windows_ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = _windows_ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    hwnd = user32.GetForegroundWindow()
    if not hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value or pid.value == os.getpid():
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    if rect.right - rect.left <= 80 or rect.bottom - rect.top <= 80:
        return None
    app = f"PID {pid.value}"
    process = kernel32.OpenProcess(0x1000, False, pid.value)
    if process:
        try:
            name = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(name))
            if kernel32.QueryFullProcessImageNameW(process, 0, name, ctypes.byref(size)):
                app = Path(name.value).stem
        finally:
            kernel32.CloseHandle(process)
    title = ctypes.create_unicode_buffer(1024)
    user32.GetWindowTextW(hwnd, title, len(title))
    return {"id": int(hwnd), "app": app, "title": title.value,
            "bounds": {"x": rect.left, "y": rect.top,
                       "width": rect.right - rect.left, "height": rect.bottom - rect.top}}


def capture_window(window_id: int, path: Path) -> None:
    from PIL import ImageGrab

    # PrintWindow returns blank frames for GPU-rendered Electron windows.
    # Read the visible foreground window's rectangle from the desktop instead.
    user32 = _windows_ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user32.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
    previous = user32.SetThreadDpiAwarenessContext(wintypes.HANDLE(-4))
    try:
        if user32.GetForegroundWindow() != window_id:
            raise RuntimeError("前台窗口已切换，跳过截图")
        rect = wintypes.RECT()
        if not user32.GetWindowRect(window_id, ctypes.byref(rect)):
            raise _windows_ctypes.WinError(_windows_ctypes.get_last_error())
        if rect.right <= rect.left or rect.bottom <= rect.top:
            raise RuntimeError("窗口截图区域无效")
        image = ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(previous)
    try:
        image.save(path)
    finally:
        image.close()
