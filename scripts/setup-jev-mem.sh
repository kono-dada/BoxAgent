#!/bin/sh

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(dirname -- "$script_dir")
venv_dir=${BOXAGENT_JEV_MEM_VENV:-${BOXAGENT_JEV_MEMORY_VENV:-"$project_root/.runtime/jev-mem-venv"}}
requirements="$project_root/boxagent/infrastructure/memory/jev_mem/requirements.txt"

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' '需要 uv 才能准备 Jev-Mem Worker。' >&2
    exit 1
fi
if [ ! -f "$requirements" ]; then
    printf '仓库内 Jev-Mem 依赖清单不存在：%s\n' "$requirements" >&2
    exit 1
fi
if [ ! -x "$venv_dir/bin/python" ]; then
    uv venv "$venv_dir" --python 3.12
fi
uv pip install --python "$venv_dir/bin/python" -r "$requirements"
printf 'BoxAgent 内置 Jev-Mem Worker 环境已就绪：%s\n' "$venv_dir"
