"""Platform dispatch and HWND capture contracts without touching the desktop."""

import asyncio
import ctypes
from ctypes import wintypes
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from boxagent.infrastructure.perception.qwen_mlx import WindowSummary, focused_window


def test_windows_foreground_dispatch() -> None:
    expected = {"id": 0x123456789, "app": "Editor"}
    with patch("sys.platform", "win32"), patch(
        "boxagent.infrastructure.perception.windows.focused_window", return_value=expected
    ) as native:
        assert focused_window() == expected
    native.assert_called_once_with()


def test_unsupported_platform_has_clear_error() -> None:
    with patch("sys.platform", "linux"), pytest.raises(RuntimeError, match="linux"):
        focused_window()


def test_windows_capture_preserves_full_hwnd(tmp_path: Path) -> None:
    observer = WindowSummary(Mock())
    target = tmp_path / "window.png"
    with patch("sys.platform", "win32"), patch(
        "boxagent.infrastructure.perception.windows.capture_window"
    ) as capture:
        asyncio.run(observer.capture(0x123456789, target))
    capture.assert_called_once_with(0x123456789, target)


def test_native_capture_targets_window_only(tmp_path: Path) -> None:
    from boxagent.infrastructure.perception.windows import capture_window

    def rectangle(window_id: int, pointer: ctypes.c_void_p) -> int:
        rect = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = -1200, 50, -200, 750
        return 1

    native = Mock()
    native.GetForegroundWindow.return_value = 123
    native.GetWindowRect.side_effect = rectangle
    native.SetThreadDpiAwarenessContext.return_value = 1
    with (
        patch("ctypes.WinDLL", return_value=native, create=True),
        patch("PIL.ImageGrab.grab") as grab,
    ):
        capture_window(123, tmp_path / "window.png")
    grab.assert_called_once_with(bbox=(-1200, 50, -200, 750), all_screens=True)
    grab.return_value.close.assert_called_once()
    native.SetThreadDpiAwarenessContext.assert_called_with(1)


def test_native_capture_rejects_switched_window(tmp_path: Path) -> None:
    from boxagent.infrastructure.perception.windows import capture_window

    native = Mock()
    native.GetForegroundWindow.return_value = 456
    with (
        patch("ctypes.WinDLL", return_value=native, create=True),
        patch("PIL.ImageGrab.grab") as grab,pytest.raises(RuntimeError, match="前台窗口已切换")
    ):
        capture_window(123, tmp_path / "window.png")
    grab.assert_not_called()
