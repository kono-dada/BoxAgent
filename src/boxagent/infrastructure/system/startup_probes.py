"""Bounded local checks run in each component's isolated Python environment."""
from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Literal, TypedDict

Component = Literal['core', 'memory', 'vision', 'prepare-memory']
EMBEDDING_REPO = 'sentence-transformers/all-MiniLM-L6-v2'
EMBEDDING_REVISION = '1110a243fdf4706b3f48f1d95db1a4f5529b4d41'


class ProbeResult(TypedDict):
    state: Literal['ready', 'missing', 'unavailable']
    code: str


def check_component(component: Component, *, data: Path, model: Path) -> ProbeResult:
    if sys.version_info[:2] != (3, 12):
        return {"state": "unavailable", "code": "python_version_mismatch"}
    if component == "core":
        for name in (
            "ssl",
            "sqlite3",
            "pyaudio",
            "websockets",
            "PIL",
            "AppKit",
            "Quartz",
            "zstandard",
        ):
            importlib.import_module(name)
        return {'state': 'ready', 'code': ''}
    if component == 'prepare-memory':
        hub = importlib.import_module('huggingface_hub')
        # The pinned snapshot is subsequently loaded by an absolute local path.
        hub.snapshot_download(repo_id=EMBEDDING_REPO, revision=EMBEDDING_REVISION,
                              local_dir=str(data / 'models/embedding'),
                              allow_patterns=['*.json', '*.txt', '*.safetensors', '1_Pooling/*'],
                              max_workers=2)
        return {'state': 'ready', 'code': ''}
    if component == 'memory':
        embedding = data / 'models/embedding'
        if not (embedding / 'model.safetensors').is_file():
            return {'state': 'missing', 'code': 'embedding_missing'}
        os.environ['BOXAGENT_EMBEDDING_MODEL'] = str(embedding)
        os.environ['HF_HUB_OFFLINE'] = '1'
        # Initialize the actual backend without touching the user's memory store.
        from boxagent.infrastructure.memory.jev_mem.worker import Worker
        with tempfile.TemporaryDirectory(prefix='boxagent-memory-probe-') as directory:
            worker = Worker(Path(directory), 'jev')
            result = worker.handle({'operation': 'health'})
            if result.get('status') != 'ready' or result.get('backend') != 'jev':
                return {'state': 'unavailable', 'code': 'memory_probe_failed'}
        return {'state': 'ready', 'code': ''}
    if not (model / 'model.safetensors').is_file():
        return {'state': 'missing', 'code': 'vision_model_missing'}
    mlx = importlib.import_module('mlx.core')
    array = mlx.array([1, 2, 3])
    mlx.eval(array)
    loader = importlib.import_module('mlx_vlm')
    loader.load(str(model))
    return {'state': 'ready', 'code': ''}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('component', choices=('core', 'memory', 'vision', 'prepare-memory'))
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    args = parser.parse_args()
    component: Component = args.component
    try:
        result = check_component(component, data=args.data, model=args.model)
    except Exception:
        # Exceptions and SDK responses may contain secrets. Only stable codes cross IPC.
        logging.getLogger(__name__).warning('Startup component check failed: %s', component)
        result: ProbeResult = {'state': 'unavailable', 'code': f'{component}_probe_failed'}
    print(json.dumps(result), flush=True)
    raise SystemExit(0 if result['state'] == 'ready' else 1)
