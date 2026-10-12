#!/usr/bin/env bash
set -euo pipefail
# Finder 双击与终端执行共用受维护的启动入口，参数原样传递。
cd "$(dirname "$0")"
exec ./scripts/run-pet.sh "$@"
