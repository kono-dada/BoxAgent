#!/usr/bin/env python3
"""Jev-Mem build/query CLI. The existing MAGMA path remains the default."""

import argparse
from dataclasses import replace
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv

from .system import JevMemSystem
from boxagent.infrastructure.memory.jev_mem.core.jev_mem_config import JevMemConfig


def main():
    parser = argparse.ArgumentParser(description='Jev-Mem memory construction and retrieval')
    parser.add_argument('--mode', choices=['build', 'query', 'test'], default='test')
    parser.add_argument('--input', help='Observation JSON for build; LoCoMo JSON for test')
    parser.add_argument('--question')
    parser.add_argument('--model', default='gpt-4o-mini')
    parser.add_argument('--embedding-model', default='minilm', choices=['minilm', 'openai'])
    parser.add_argument('--cache-dir', default='./cache')
    parser.add_argument('--jev-mem', dest='jev_mem', action='store_true')
    parser.add_argument('--jev-config', dest='jev_config')
    parser.add_argument('--jev-mock', action='store_true')
    parser.add_argument('--no-jev-write', dest='no_jev_write', action='store_true')
    parser.add_argument('--no-jev-read', dest='no_jev_read', action='store_true')
    args = parser.parse_args()
    if args.mode in ('build', 'test') and not args.input:
        parser.error('--input is required for build/test')
    if args.mode == 'query' and not args.question:
        parser.error('--question is required for query')
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    if args.mode == 'test':
        command = [sys.executable, '-m', 'jev_mem.benchmarks.locomo',
                   '--dataset', args.input, '--model', args.model, '--embedding-model', args.embedding_model,
                   '--cache-dir', args.cache_dir]
        for flag in ('jev_mem', 'jev_mock', 'no_jev_write', 'no_jev_read'):
            if getattr(args, flag):
                command.append('--' + flag.replace('_', '-'))
        if args.jev_config:
            command.extend(['--jev-config', args.jev_config])
        return subprocess.call(command)
    overrides = {}
    if args.jev_mem:
        overrides.update(write_enabled=True, read_enabled=True)
    if args.jev_mock:
        overrides['jev_mock'] = True
    if args.no_jev_write:
        overrides['write_enabled'] = False
    if args.no_jev_read:
        overrides['read_enabled'] = False
    config = JevMemConfig.load(args.jev_config, **overrides)
    cache_dir = Path(args.cache_dir)
    if config.write_enabled or config.read_enabled:
        digest = hashlib.sha256(json.dumps(config.to_dict(), sort_keys=True).encode()).hexdigest()[:12]
        cache_dir /= 'jev_mem_' + digest
        config = replace(config, audit_path=config.audit_path or str(cache_dir / 'decisions.jsonl'))
    system = JevMemSystem(args.model, args.embedding_model, str(cache_dir), config)
    if args.mode == 'build':
        if (cache_dir / 'graph.json').exists():
            system.load_memory()
        data = json.loads(Path(args.input).read_text())
        print(json.dumps(system.build_memory_from_conversation(data)))
        system.save_memory()
    else:
        if not (cache_dir / 'graph.json').exists():
            parser.error('No saved graph in ' + str(cache_dir))
        system.load_memory()
        print(system.query(args.question))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
