#!/bin/sh

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(dirname -- "$script_dir")

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' '未找到 uv，请先安装 uv 并确认它在 PATH 中。' >&2
    exit 1
fi

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
    if [ ! -f "$project_root/.env.local" ] || \
            ! grep -Eq '^DEEPSEEK_API_KEY=.+$' "$project_root/.env.local"; then
        printf '%s\n' '未配置 DEEPSEEK_API_KEY，请先写入项目根目录的 .env.local。' >&2
        exit 1
    fi
fi

cd "$project_root"
exec uv run --script scripts/pet.py \
    --task-provider deepseek \
    --task-model "${BOXAGENT_DEEPSEEK_MODEL:-deepseek-flash}" \
    "$@"
