"""Resolve developer and packaged WeChat configuration without resource writes."""
from __future__ import annotations

import json
import os
from pathlib import Path

from boxagent.bootstrap.settings import Settings, read_optional_setting, read_optional_secret
from boxagent.bootstrap.wechat import WeChatConfig


def wechat_config(settings: Settings) -> WeChatConfig:
    defaults = WeChatConfig()
    packaged = os.environ.get('BOXAGENT_PACKAGED') == '1'
    def path(name: str, fallback: Path) -> Path:
        return Path(read_optional_setting(name) or fallback).expanduser().resolve()
    material: dict[str, str] | None = None
    if raw := read_optional_secret('BOXAGENT_WECHAT_KEY_MATERIAL'):
        value: object = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get('keys'), dict):
            raise ValueError('wechat_keys_invalid')
        material = {}
        for name, key in value['keys'].items():
            if not isinstance(name, str) or not isinstance(key, str):
                raise ValueError('wechat_keys_invalid')
            material[name] = key
    root = read_optional_setting('BOXAGENT_WECHAT_DB_ROOT')
    report = read_optional_setting('BOXAGENT_WECHAT_KEYS')
    return WeChatConfig(
        app=path('BOXAGENT_WECHAT_APP', settings.data_dir/'integrations/wechat/WeChat-4.1.15-BoxAgent.app'
                 if packaged else defaults.app),
        db_root=Path(root).expanduser().resolve() if root else None,
        key_report=Path(report).expanduser().resolve() if report else None if packaged else defaults.key_report,
        data_dir=settings.data_dir/'wechat', key_material=material,
        lldb=read_optional_setting('BOXAGENT_WECHAT_LLDB') or '/usr/bin/lldb',
        interval=float(read_optional_setting('BOXAGENT_WECHAT_POLL_INTERVAL') or '0.5'))
