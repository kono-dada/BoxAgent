#!/bin/sh

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(dirname -- "$script_dir")
BOXAGENT_ROOT=$project_root
. "$script_dir/runtime-env.sh"
venv_dir=${BOXAGENT_JEV_MEM_VENV:-${BOXAGENT_JEV_MEMORY_VENV:-$(dirname -- "$(dirname -- "$BOXAGENT_JEV_MEM_PYTHON")")}}
requirements="$script_dir/requirements/jev-mem.lock.txt"

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' '需要 uv 才能准备 Jev-Mem Worker。' >&2
    exit 1
fi
if [ ! -f "$requirements" ]; then
    printf '仓库内 Jev-Mem 依赖清单不存在：%s\n' "$requirements" >&2
    exit 1
fi
if [ ! -x "$venv_dir/bin/python" ]; then
    uv venv "$venv_dir" --python 3.12.9
fi
uv pip sync --python "$venv_dir/bin/python" "$requirements"
uv pip check --python "$venv_dir/bin/python"
printf 'BoxAgent 内置 Jev-Mem Worker 环境已就绪：%s\n' "$venv_dir"
