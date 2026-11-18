"""Build-machine composition root for the pinned client archive."""
from pathlib import Path

from boxagent.infrastructure.wechat.distribution import ArchiveManifest, build_client_archive


def prepare_wechat_distribution(source: Path, output: Path) -> ArchiveManifest:
    return build_client_archive(source, output)
