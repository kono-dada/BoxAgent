#!/bin/sh

set -eu

revision=7ab0c73c6d8f4f611ad252c1e6ba8083f8df0e44
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(dirname -- "$script_dir")
source_dir=${BOXAGENT_JEV_MEMORY_SOURCE:-"$project_root/.runtime/jev-mem-src"}
venv_dir=${BOXAGENT_JEV_MEMORY_VENV:-"$project_root/.runtime/jev-mem-venv"}

if ! command -v git >/dev/null 2>&1 || ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' '需要 git 和 uv 才能准备 Jev-Mem Worker。' >&2
    exit 1
fi

mkdir -p "$(dirname -- "$source_dir")" "$(dirname -- "$venv_dir")"
if [ ! -e "$source_dir" ]; then
    git clone --depth 1 https://github.com/libingzheren/Jev-Mem.git "$source_dir"
fi
if [ ! -d "$source_dir/.git" ]; then
    printf '目标路径已存在但不是 Git 仓库：%s\n' "$source_dir" >&2
    exit 1
fi

current=$(git -C "$source_dir" rev-parse HEAD)
if [ "$current" != "$revision" ]; then
    printf '当前 Jev-Mem 提交与 BoxAgent 验证版本不同：%s\n' "$current" >&2
    printf '期望：%s。为避免覆盖本地改动，脚本不会自动切换版本。\n' "$revision" >&2
    exit 1
fi

if [ ! -x "$venv_dir/bin/python" ]; then
    uv venv "$venv_dir" --python 3.12
fi
uv pip install --python "$venv_dir/bin/python" -e "$source_dir"
printf 'Jev-Mem Worker 环境已就绪：%s\n' "$venv_dir"
