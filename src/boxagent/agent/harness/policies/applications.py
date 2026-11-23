"""Application boundaries shared by dynamic and native Computer Use paths."""
from __future__ import annotations

from collections.abc import Mapping

WECHAT_CU_DENIED = (
    "wechat_cu_forbidden: 禁止通过 Computer Use 操作微信或微信副本。"
    "请使用 wechat_* 专用工具；接口不可用时报告具体原因，不得退回截图、点击或键盘操作。"
)


def is_wechat_reference(reference: object) -> bool:
    if not isinstance(reference, str):
        return False
    value = reference.casefold()
    return any(marker in value for marker in ("wechat", "weixin", "微信"))


def computer_use_denial(arguments: Mapping[str, object]) -> str | None:
    return WECHAT_CU_DENIED if is_wechat_reference(arguments.get("app")) else None
