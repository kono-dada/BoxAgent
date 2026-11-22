"""Build-time archive of an unchanged Tencent-signed client, with a pinned manifest.

The debuggable copy is created on the user's Mac, outside the notarized BoxAgent
bundle. This archive contains no account data and no get-task-allow modification.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TypedDict

from boxagent.infrastructure.wechat.addresses import BUILD, VERSION
from boxagent.infrastructure.wechat.replica import check_client


class ArchiveManifest(TypedDict):
    version: int
    clientVersion: str
    build: str
    bundledPath: str
    url: str | None
    sha256: str
    size: int


def verify_archive(archive: Path) -> None:
    """Only mount this explicitly selected image read-only, and never launch it."""
    temporary = Path(tempfile.mkdtemp(prefix='boxagent-client-archive-'))
    mount = temporary/'volume'
    mount.mkdir()
    attached = False
    try:
        subprocess.run(
            [
                "/usr/bin/hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-noautoopen",
                "-mountpoint",
                str(mount),
                str(archive),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        attached = True
        apps = list(mount.glob('*.app'))
        if len(apps) != 1:
            raise ValueError('wechat_client_missing')
        check_client(apps[0], replica=False)
    finally:
        if attached or os.path.ismount(mount):
            detached = subprocess.run(
                ["/usr/bin/hdiutil", "detach", str(mount)], capture_output=True, timeout=30
            )
            if detached.returncode:
                raise ValueError('wechat_archive_detach_failed')
        # Deliberately never recurse into the mount, even after a failed attach.
        mount.rmdir()
        temporary.rmdir()


def build_client_archive(source: Path, output: Path) -> ArchiveManifest:
    source = source.expanduser().resolve(strict=True)
    if source.suffix == '.dmg':
        verify_archive(source)
    else:
        check_client(source, replica=False)
    output.mkdir(parents=True, exist_ok=True)
    name = f'{VERSION}.{BUILD}.dmg'
    archive = output/name
    if source.suffix == '.dmg' and source != archive.resolve():
        staged_image = output/(name+'.tmp')
        shutil.copyfile(source, staged_image)
        os.replace(staged_image, archive)
    elif archive.exists():
        verify_archive(archive)
    else:
        with tempfile.TemporaryDirectory(prefix='.client-stage-', dir=output) as temporary:
            stage = Path(temporary)
            original = stage/'source/WeChat.app'
            original.parent.mkdir()
            subprocess.run(['/usr/bin/ditto', str(source), str(original)],
                           check=True, capture_output=True, timeout=180)
            check_client(original, replica=False)
            image = stage/name
            subprocess.run(['/usr/bin/hdiutil', 'create', '-volname', 'BoxAgent WeChat Source',
                            '-srcfolder', str(original.parent), '-format', 'UDZO', str(image)],
                           check=True, capture_output=True, timeout=300)
            verify_archive(image)
            os.rename(image, archive)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest: ArchiveManifest = {
        "version": 1,
        "clientVersion": VERSION,
        "build": BUILD,
        "bundledPath": "wechat/" + name,
        "url": None,
        "sha256": digest,
        "size": archive.stat().st_size,
    }
    temporary_manifest = output / "manifest.json.tmp"
    temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    os.replace(temporary_manifest, output / "manifest.json")
    return manifest
