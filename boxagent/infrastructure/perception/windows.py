"""Windows foreground window access; loaded only on Windows."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from typing import TypedDict


class WindowInfo(TypedDict):
    id: int
    app: str


def focused_window() -> WindowInfo | None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
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
    return {"id": int(hwnd), "app": app}


def capture_window(window_id: int, path: Path) -> None:
    from PIL import ImageGrab

    # Pillow uses the native Windows window capture API for the supplied HWND.
    image = ImageGrab.grab(window=window_id)
    try:
        image.save(path)
    finally:
        image.close()
