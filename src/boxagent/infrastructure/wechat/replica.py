"""Prepare a separately identified, build-verified local client copy."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
from typing import cast

from boxagent.infrastructure.wechat.addresses import BUILD, BUNDLE_ID, VERSION
from boxagent.infrastructure.wechat.probe import disk_image


def check_client(app: Path, *, replica: bool = True) -> None:
    app = app.resolve(strict=True)
    info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
    if (info.get('CFBundleShortVersionString'), str(info.get('CFBundleVersion'))) != (VERSION, BUILD):
        raise ValueError('wechat_unsupported_build')
    if replica and info.get('CFBundleIdentifier') != BUNDLE_ID:
        raise ValueError('wechat_not_replica')
    disk_image(app/'Contents/Resources/wechat.dylib')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
                   check=True, capture_output=True, timeout=30)
    if not replica:
        signature = subprocess.run(['/usr/bin/codesign', '-dv', str(app)],
                                   check=True, capture_output=True, timeout=10).stderr
        if b'TeamIdentifier=5A4RE8SF68' not in signature:
            raise ValueError('wechat_source_signature_invalid')
    if replica:
        entitlements = subprocess.run(['/usr/bin/codesign', '-d', '--entitlements', ':-', str(app)],
                                      check=True, capture_output=True, timeout=10).stdout
        values = plistlib.loads(entitlements)
        if values.get('com.apple.security.get-task-allow') is not True:
            raise ValueError('wechat_debug_permission_missing')
        if 'com.apple.developer.team-identifier' in values:
            raise ValueError('wechat_replica_signature_invalid')


def replica_entitlements(raw: bytes) -> dict[str, object]:
    value: object = plistlib.loads(raw) if raw.strip() else {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError('wechat_replica_signature_invalid')
    entitlements = cast(dict[str, object], value)
    # An ad-hoc copy cannot claim Tencent's restricted signing-team entitlement.
    # Keep App Sandbox and the separately named container/group unchanged.
    entitlements.pop('com.apple.developer.team-identifier', None)
    entitlements['com.apple.security.get-task-allow'] = True
    for name in ('com.apple.application-identifier', 'application-identifier'):
        identifier = entitlements.get(name)
        if isinstance(identifier, str):
            team = identifier.split('.', 1)[0]
            entitlements[name] = team+'.'+BUNDLE_ID
    groups = entitlements.get('com.apple.security.application-groups', [])
    if isinstance(groups, list):
        entitlements['com.apple.security.application-groups'] = [
            group if BUNDLE_ID in group else group.replace('com.tencent.xinWeChat', BUNDLE_ID)
            for group in groups if isinstance(group, str)]
    return entitlements


def sign_replica(app: Path, raw: bytes) -> None:
    with tempfile.TemporaryDirectory(prefix='.wechat-sign-', dir=app.parent) as directory:
        file = Path(directory)/'entitlements.plist'
        file.write_bytes(plistlib.dumps(replica_entitlements(raw)))
        subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', '--entitlements',
                        str(file), str(app)], check=True, capture_output=True, timeout=120)


def prepare_client(source: Path, destination: Path) -> Path:
    """Never changes the selected source or disables Gatekeeper/quarantine checks."""
    source = source.expanduser().resolve(strict=True)
    destination = destination.expanduser().absolute()
    check_client(source, replica=False)
    if destination.is_symlink() or source == destination or source in destination.parents:
        raise ValueError('wechat_destination_invalid')
    if destination.exists():
        try:
            check_client(destination)
        except ValueError as error:
            if str(error) != 'wechat_replica_signature_invalid':
                raise
            raw = subprocess.run(['/usr/bin/codesign', '-d', '--entitlements', ':-', str(destination)],
                                 check=True, capture_output=True, timeout=10).stdout
            sign_replica(destination, raw)
            check_client(destination)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.wechat-prepare-', dir=destination.parent) as temporary:
        staged = Path(temporary)/destination.name
        subprocess.run(['/usr/bin/ditto', str(source), str(staged)], check=True, capture_output=True, timeout=180)
        info_path = staged/'Contents/Info.plist'
        info = plistlib.loads(info_path.read_bytes())
        info.update(CFBundleIdentifier=BUNDLE_ID, CFBundleName='WeChat-BoxAgent',
                    CFBundleDisplayName='WeChat-BoxAgent', SUEnableAutomaticChecks=False,
                    SUAutomaticallyUpdate=False)
        info_path.write_bytes(plistlib.dumps(info))
        raw = subprocess.run(['/usr/bin/codesign', '-d', '--entitlements', ':-', str(source)],
                             check=True, capture_output=True, timeout=10).stdout
        sign_replica(staged, raw)
        check_client(staged)
        os.rename(staged, destination)
    return destination
