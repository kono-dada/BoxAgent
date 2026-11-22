import importlib.util
import zipfile
from pathlib import Path

import pytest


def test_packaged_wheel_metadata_ignores_vendored_distributions(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / 'scripts/prepare-packaged-runtime.py'
    spec = importlib.util.spec_from_file_location('packaging_builder', script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    wheel = tmp_path / "setuptools.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "setuptools-84.0.0.dist-info/METADATA", "Name: setuptools\nVersion: 84.0.0\n"
        )
        archive.writestr(
            "setuptools/_vendor/other-1.0.dist-info/METADATA", "Name: other\nVersion: 1.0\n"
        )
    assert module.wheel_identity(wheel) == ("setuptools", "84.0.0")


def test_sdk_archive_metadata_does_not_break_strict_app_signing(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / 'scripts/prepare-packaged-runtime.py'
    spec = importlib.util.spec_from_file_location('packaging_builder', script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sdk = tmp_path / 'native/aoq/PluginOpus.framework'
    sdk.mkdir(parents=True)
    metadata = sdk.parent / '__MACOSX/PluginOpus.framework'
    metadata.mkdir(parents=True)
    (sdk / 'Headers').symlink_to('Versions/Current/Headers')
    module.clean_aoq(tmp_path)
    assert not metadata.exists()
    assert not (sdk / 'Headers').is_symlink()
    # A missing runtime binary must still fail preparation.
    (sdk / 'PluginOpus').symlink_to('Versions/Current/PluginOpus')
    with pytest.raises(ValueError, match='Broken SDK link'):
        module.clean_aoq(tmp_path)
