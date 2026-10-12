#!/bin/sh
# 可重复执行的本机安装入口；凭据与系统授权由用户配置。
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BOXAGENT_ROOT=$(dirname -- "$script_dir")
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
. "$script_dir/runtime-env.sh"
cd "$BOXAGENT_ROOT"
case "${1:-}" in
    --check) exec "$script_dir/run-pet.sh" --check-install ;;
    --help)
        printf '%s\n' '用法：./scripts/install.sh [--check]' '不带参数：安装锁定的依赖、模型、AOQ、3D 资源和伙伴控制台。' '--check：只检查本机安装和凭据是否齐备，不调用云端或操作应用。'
        exit 0 ;;
    '') ;;
    *) printf '未知参数：%s\n' "$1" >&2; exit 2 ;;
esac
if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
    printf '%s\n' '安装入口目前仅支持 Apple Silicon macOS。' >&2
    exit 1
fi
for install_tool in uv pnpm git curl jq shasum codesign pkg-config; do
    if ! command -v "$install_tool" >/dev/null 2>&1; then
        printf '缺少工具：%s；请先按 docs/setup.md 准备系统依赖。\n' "$install_tool" >&2
        exit 1
    fi
done
git lfs version >/dev/null
if ! pkg-config --exists portaudio-2.0; then
    printf '%s\n' '缺少 PortAudio 或 pkg-config，请先按 docs/setup.md 准备系统依赖。' >&2
    exit 1
fi
uv python install 3.12.9
uv sync --locked --script scripts/pet.py
"$script_dir/setup-jev-mem.sh"
"$script_dir/setup-vision.sh"
aoq_frameworks=${BOXAGENT_AOQ_SDK_DIR:-${BOXAGENT_AOQ_INSTALL_DIR:-$BOXAGENT_ROOT/.runtime/aoq-sdk/1.3.0}/frameworks}
if [ ! -d "$aoq_frameworks/AoqClientSdk.framework" ] || [ ! -d "$aoq_frameworks/PluginOpus.framework" ]; then
    zsh "$script_dir/setup-aoq-sdk.sh"
fi
zsh "$script_dir/download-qwen-mlx.zsh"
bash "$script_dir/setup_vrm.sh"
pnpm --dir web/panel install --frozen-lockfile --store-dir "$BOXAGENT_PNPM_STORE"
pnpm --dir web/panel build
exec "$script_dir/run-pet.sh" --check-install
