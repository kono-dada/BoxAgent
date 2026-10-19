"""Platform dispatch and HWND capture contracts without touching the desktop."""

import asyncio
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

    with patch("PIL.ImageGrab.grab") as grab:
        capture_window(123, tmp_path / "window.png")
    grab.assert_called_once_with(window=123)
    grab.return_value.close.assert_called_once()
