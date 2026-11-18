# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pip==25.3", "delocate==0.13.0", "wheel==0.45.1"]
# ///
"""Prepare relocatable, hash-checked macOS Python resources on the build machine."""
from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import TypedDict
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = '3.12.9'
GROUPS = ('core', 'jev', 'vision')
WHEEL_RECIPE = 'macos14-split-mlx-v2-pip25.3-delocate0.13.0-wheel0.45.1'


class FileRecord(TypedDict):
    path: str
    sha256: str


def digest(file: Path) -> str:
    with file.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def command(arguments: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(arguments, cwd=ROOT, env=env, check=True)


def wheel_identity(file: Path) -> tuple[str, str]:
    with zipfile.ZipFile(file) as archive:
        names = [name for name in archive.namelist() if len(Path(name).parts) == 2 and name.endswith('.dist-info/METADATA')]
        if len(names) != 1 or archive.testzip() is not None:
            raise ValueError(f'Invalid wheel: {file.name}')
        metadata = email.message_from_bytes(archive.read(names[0]))
        name, version = metadata.get('Name'), metadata.get('Version')
        if not isinstance(name, str) or not isinstance(version, str):
            raise ValueError('Wheel metadata missing')
        return name, version


def sign_wheel(file: Path, output: Path, *, identity: str) -> Path:
    with zipfile.ZipFile(file) as archive:
        native = any(archive.read(name)[:4] in {b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}
                     for name in archive.namelist() if not name.endswith('/') and name.endswith(('.so', '.dylib')))
    if not native:
        shutil.copy2(file, output / file.name)
        return output / file.name
    with tempfile.TemporaryDirectory(prefix='boxagent-wheel-') as temporary:
        directory = Path(temporary)
        command([sys.executable, '-m', 'wheel', 'unpack', str(file), '--dest', str(directory)])
        unpacked = next(directory.iterdir())
        for member in unpacked.rglob('*'):
            if member.is_file() and not member.is_symlink():
                with member.open('rb') as stream:
                    magic = stream.read(4)
                if magic in {b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}:
                    args = ['codesign', '--force', '--sign', identity]
                    if identity != '-':
                        args.extend(['--options', 'runtime', '--timestamp'])
                    command([*args, str(member)])
        command([sys.executable, '-m', 'wheel', 'pack', str(unpacked), '--dest-dir', str(output)])
    return output / file.name


def prepare_wheels(group: str, output: Path, *, identity: str) -> None:
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix=f'boxagent-{group}-') as temporary:
        working = Path(temporary)
        source = working / 'source'
        source.mkdir()
        repaired = working / 'repaired'
        repaired.mkdir()
        requirements = working / 'requirements.txt'
        if group == 'core':
            command(['uv', 'export', '--script', 'scripts/pet.py', '--locked', '--output-file', str(requirements)])
        else:
            requirements.write_bytes((ROOT / f'scripts/requirements/{"jev-mem" if group == "jev" else "vision"}.lock.txt').read_bytes())
        if group == 'core':
            # PyAudio is built on the maker's machine and its PortAudio is vendored below.
            command([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-cache-dir',
                     '--wheel-dir', str(source), '-r', str(requirements)],
                    env={**os.environ, 'MACOSX_DEPLOYMENT_TARGET': '14.0'})
        else:
            command([sys.executable, '-m', 'pip', 'download', '--no-deps', '--only-binary=:all:',
                     '--platform', 'macosx_14_0_arm64', '--python-version', '312', '--implementation', 'cp',
                     '--abi', 'cp312', '--dest', str(source), '-r', str(requirements)])
        identities = dict(wheel_identity(wheel) for wheel in source.glob('*.whl'))
        if 'mlx' in identities and identities.get('mlx-metal') != identities['mlx']:
            raise RuntimeError('MLX requires its matching mlx-metal wheel')
        provenance: dict[str, str] = {}
        for wheel in sorted(source.glob('*.whl')):
            provenance[wheel.name] = digest(wheel)
            # MLX intentionally loads libmlx from the matching mlx-metal namespace wheel.
            # Delocating it alone would reject this valid cross-wheel dependency or
            # duplicate the library without its adjacent Metal shader resources.
            if wheel_identity(wheel)[0] == 'mlx':
                shutil.copy2(wheel, repaired / wheel.name)
                continue
            # delocate vendors Homebrew/other non-system libraries and repairs load paths.
            command([sys.executable, '-m', 'delocate.cmd.delocate_wheel', '--require-archs', 'arm64',
                     '--wheel-dir', str(repaired), str(wheel)])
        final = output / 'wheels'
        final.mkdir()
        locks: list[str] = []
        for wheel in sorted(repaired.glob('*.whl')):
            signed = sign_wheel(wheel, final, identity=identity)
            name, version = wheel_identity(signed)
            locks.append(f'{name}=={version} --hash=sha256:{digest(signed)}\n')
        if not locks:
            raise RuntimeError(f'No wheels for {group}')
        (output / 'requirements.lock').write_text(''.join(locks))
        (output / 'input-wheels.json').write_text(json.dumps(provenance, indent=2) + '\n')


def copy_source(stage: Path) -> None:
    target = stage / 'boxagent'
    target.mkdir()
    ignored = shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store')
    for directory in ('boxagent', 'assets', 'skills'):
        shutil.copytree(ROOT / directory, target / directory, ignore=ignored)
    scripts = target / 'scripts'
    scripts.mkdir()
    for name in ('jev_mem_worker.py', 'window_summary_worker.py', 'intent_worker.py', 'codex_computer_use.py',
                 'computer_use.py', 'computer-use-mcp-client.mjs', 'verify-computer-use.mjs'):
        shutil.copy2(ROOT / 'scripts' / name, scripts / name)
    shutil.copytree(ROOT / 'scripts/model-manifests', scripts / 'model-manifests')
    shutil.copytree(ROOT / 'scripts/wechat-clients', scripts / 'wechat-clients')
    if not (target / 'assets/vrm/viewer.js').is_file():
        raise RuntimeError('Build the VRM viewer first: pnpm --dir web/vrm build')
    for file in (target / 'assets').rglob('*'):
        if file.is_file() and file.stat().st_size < 200 and file.read_bytes().startswith(b'version https://git-lfs.github.com'):
            raise RuntimeError(f'Unresolved Git LFS asset: {file.relative_to(target)}')


def prepare_notices(resources: Path) -> None:
    """Keep upstream license texts from the actual shipped wheels beside the app."""
    notices = resources / 'third-party-notices'
    if notices.exists():
        shutil.rmtree(notices)
    shutil.copytree(ROOT / 'scripts/packaging', notices)
    electron = ROOT / 'web/panel/node_modules/electron/dist'
    for name in ('LICENSE', 'LICENSES.chromium.html'):
        shutil.copy2(electron / name, notices / f'Electron-{name}')
    inventory: list[str] = []
    for group in GROUPS:
        for wheel in sorted((resources / f'python-dependencies/{group}/wheels').glob('*.whl')):
            name, version = wheel_identity(wheel)
            inventory.append(f'- {group}: {name} {version}')
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.namelist():
                    parts = Path(member).parts
                    if len(parts) < 2 or not parts[0].endswith('.dist-info') or member.endswith('/'):
                        continue
                    if '..' in parts or Path(member).is_absolute():
                        raise ValueError('Invalid wheel license path')
                    if parts[1] == 'licenses' or any(word in Path(member).name.lower() for word in ('license', 'copying', 'notice')):
                        target = notices / group / f'{name}-{version}' / Path(*parts[1:])
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(archive.read(member))
    (notices / 'python-packages.md').write_text('\n'.join(inventory) + '\n')


def clean_aoq(resources: Path) -> None:
    """Remove archive metadata and an unused dangling SDK header alias."""
    aoq = resources / 'native/aoq'
    for entry in sorted(aoq.rglob('*'), key=lambda path: len(path.parts), reverse=True):
        if entry.name == '__MACOSX' or entry.name == '.DS_Store' or entry.name.startswith('._'):
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        elif entry.is_symlink() and not entry.exists():
            if entry.name == 'Headers':
                entry.unlink()
            else:
                raise ValueError(f'Broken SDK link: {entry.relative_to(aoq)}')


def prepare(output: Path, *, identity: str, aoq: Path | None,
            wechat_source: Path = Path('/Applications/WeChat.app')) -> None:
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Build on Apple Silicon macOS')
    output = output.resolve()
    allowed = (ROOT / '.runtime').resolve()
    if not output.is_relative_to(allowed) or output == allowed:
        raise ValueError('Staging must be a dedicated subdirectory of .runtime')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='package-stage-', dir=output.parent) as temporary:
        stage = Path(temporary)
        uv = shutil.which('uv')
        if not uv:
            raise RuntimeError('uv is required on the build machine')
        install = stage / 'managed-python'
        command([uv, 'python', 'install', '--install-dir', str(install), PYTHON_VERSION])
        python_root = next(path for path in install.iterdir() if path.is_dir() and path.name.startswith('cpython-'))
        shutil.copytree(python_root, stage / 'python', symlinks=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.rmtree(install)
        shutil.copy2(uv, stage / 'uv')
        copy_source(stage)
        command([sys.executable, '-m', 'boxagent.entrypoints.wechat_distribution',
                 '--source', str(wechat_source), '--output', str(stage / 'wechat')])
        (stage / 'build-identity.json').write_text(json.dumps({'wheelSigningIdentity': identity}) + '\n')
        if aoq:
            for framework in ('AoqClientSdk.framework', 'PluginOpus.framework'):
                if not (aoq / framework).is_dir():
                    raise RuntimeError(f'Missing AOQ framework: {framework}')
            for framework in ('AoqClientSdk.framework', 'PluginOpus.framework'):
                shutil.copytree(aoq / framework, stage / 'native/aoq' / framework, symlinks=True,
                                ignore=shutil.ignore_patterns('__MACOSX', '.DS_Store', '._*'))
        else:
            raise RuntimeError('Provide official AOQ frameworks with --aoq-dir; do not ship a silent fallback')
        for group in GROUPS:
            lock = ROOT / ('scripts/pet.py.lock' if group == 'core' else f'scripts/requirements/{"jev-mem" if group == "jev" else "vision"}.lock.txt')
            recipe = hashlib.sha256((digest(lock) + PYTHON_VERSION + identity + WHEEL_RECIPE).encode()).hexdigest()
            cached = ROOT / '.runtime/package-cache' / group / recipe
            complete = cached / 'complete.json'
            if not complete.is_file():
                cached.parent.mkdir(parents=True, exist_ok=True)
                if cached.exists():
                    shutil.rmtree(cached)
                prepare_wheels(group, cached, identity=identity)
                complete.write_text(json.dumps({'files': [{'path': p.relative_to(cached).as_posix(), 'sha256': digest(p)}
                    for p in sorted(cached.rglob('*')) if p.is_file()]}) + '\n')
            inventory = json.loads(complete.read_text())
            if any(digest(cached / record['path']) != record['sha256'] for record in inventory['files']):
                raise RuntimeError(f'Invalid prepared cache for {group}; remove only this cache directory: {cached}')
            shutil.copytree(cached, stage / 'python-dependencies' / group, ignore=shutil.ignore_patterns('complete.json'))
        clean_aoq(stage)
        prepare_notices(stage)
        refresh_manifest(stage)
        if output.exists():
            # Only this explicitly validated staging directory is replaced.
            shutil.rmtree(output)
        shutil.copytree(stage, output, symlinks=True)


def refresh_manifest(resources: Path) -> None:
    """Recompute identities after signing; Engine source is independent of venv identity."""
    records: list[FileRecord] = []
    for file in sorted(resources.rglob('*')):
        if file.is_file() and not file.is_symlink() and file.name != 'runtime-manifest.json':
            records.append({'path': file.relative_to(resources).as_posix(), 'sha256': digest(file)})
    python_hash = hashlib.sha256(json.dumps([r for r in records if r['path'].startswith('python/')], sort_keys=True).encode()).hexdigest()
    groups = {group: hashlib.sha256((python_hash + digest(resources / f'python-dependencies/{group}/requirements.lock')).encode()).hexdigest() for group in GROUPS}
    (resources / 'runtime-manifest.json').write_text(json.dumps({'version': 1, 'pythonVersion': PYTHON_VERSION,
        'platform': 'macos-arm64', 'groups': groups, 'files': records}, indent=2) + '\n')


def refresh_source(resources: Path, *, wechat_source: Path = Path('/Applications/WeChat.app')) -> None:
    resources = resources.resolve()
    if not resources.is_relative_to(ROOT / '.runtime'):
        raise ValueError('Source refresh is limited to .runtime staging')
    clean_aoq(resources)
    command([sys.executable, '-m', 'boxagent.entrypoints.wechat_distribution',
             '--source', str(wechat_source), '--output', str(resources / 'wechat')])
    with tempfile.TemporaryDirectory(prefix='boxagent-source-') as temporary:
        stage = Path(temporary)
        copy_source(stage)
        shutil.rmtree(resources / 'boxagent')
        shutil.copytree(stage / 'boxagent', resources / 'boxagent')
    prepare_notices(resources)
    refresh_manifest(resources)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.runtime/package-resources')
    parser.add_argument('--aoq-dir', type=Path)
    archive = ROOT / '.runtime/package-resources/wechat/4.1.15.270115.dmg'
    parser.add_argument('--wechat-source', type=Path, default=archive if archive.is_file() else Path('/Applications/WeChat.app'))
    parser.add_argument('--sign-identity', default='-')
    parser.add_argument('--refresh-manifest', type=Path)
    parser.add_argument('--refresh-source', type=Path)
    args = parser.parse_args()
    if args.refresh_source:
        refresh_source(args.refresh_source, wechat_source=args.wechat_source)
    elif args.refresh_manifest:
        refresh_manifest(args.refresh_manifest)
    else:
        prepare(args.output, identity=args.sign_identity, aoq=args.aoq_dir, wechat_source=args.wechat_source)


if __name__ == '__main__':
    main()
