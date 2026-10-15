#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BOXAGENT_ROOT=$(dirname -- "$script_dir")
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
. "$script_dir/runtime-env.sh"
cd "$BOXAGENT_ROOT"

# The desktop host reads Vite's dist directly. Rebuild only when its source
# fingerprint changed, so a source checkout never silently launches stale UI.
panel_dir="$BOXAGENT_ROOT/web/panel"
panel_marker="$panel_dir/dist/.source-fingerprint"
if [ -d "$panel_dir/src" ]; then
    panel_fingerprint=$(
        {
            find "$panel_dir/src" -type f -exec cksum {} \;
            for panel_source in \
                "$panel_dir/index.html" \
                "$panel_dir/companion.html" \
                "$panel_dir/pet.html" \
                "$panel_dir/tasks.html" \
                "$panel_dir/style.css" \
                "$panel_dir/package.json" \
                "$panel_dir/tsconfig.json" \
                "$panel_dir/vite.config.mjs"
            do
                [ ! -f "$panel_source" ] || cksum "$panel_source"
            done
        } | LC_ALL=C sort | cksum | awk '{ print $1 ":" $2 }'
    )
    panel_built_fingerprint=
    [ ! -f "$panel_marker" ] || panel_built_fingerprint=$(cat "$panel_marker")
    if [ ! -f "$panel_dir/dist/companion.html" ] ||
        [ "$panel_fingerprint" != "$panel_built_fingerprint" ]; then
        if ! command -v pnpm >/dev/null 2>&1; then
            printf '%s\n' '前端源码已更新，但找不到 pnpm。请先运行 ./scripts/install.sh。' >&2
            exit 1
        fi
        printf '%s\n' '检测到伙伴界面更新，正在构建…'
        pnpm --dir "$panel_dir" run build
        printf '%s\n' "$panel_fingerprint" > "$panel_marker"
    fi
fi
unset panel_dir panel_marker panel_fingerprint panel_built_fingerprint panel_source

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' '找不到 uv，请按 docs/setup.md 安装后重试。' >&2
    exit 1
fi
# 显式配置优先；否则复用已安装的 3.12，不因 .python-version 下载补丁版本。
if [ -z "${UV_PYTHON:-}" ]; then
    UV_PYTHON=$(uv python find --no-python-downloads 3.12 2>/dev/null) ||
        UV_PYTHON=$(env -u UV_PYTHON_INSTALL_DIR -u UV_PYTHON_BIN_DIR uv python find --no-python-downloads 3.12 2>/dev/null) || {
            printf '%s\n' '没有可用的 Python 3.12。请先运行 ./scripts/install.sh 安装运行环境。' >&2
            exit 1
        }
fi
# 接受 uv 的版本名称与解释器路径，但拒绝不兼容版本和失效路径。
runtime_python=$(uv python find --no-python-downloads "$UV_PYTHON" 2>/dev/null) || {
    printf '指定的 Python 不可用：%s；需要已安装的 Python 3.12。\n' "$UV_PYTHON" >&2
    exit 1
}
if ! "$runtime_python" -I -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))'; then
    printf '解释器不兼容：%s；需要 Python 3.12。\n' "$runtime_python" >&2
    exit 1
fi
UV_PYTHON=$runtime_python
export UV_PYTHON
if [ "${1:-}" = --check-install ]; then
    exec uv run --offline --no-python-downloads --no-sync --locked --script scripts/pet.py "$@"
fi
exec uv run --no-python-downloads --locked --script scripts/pet.py "$@"
