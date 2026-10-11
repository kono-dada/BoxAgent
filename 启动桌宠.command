#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
# 将依赖和模型缓存保存在当前工作区。
export UV_CACHE_DIR="$PWD/.runtime/uv-cache"
export HF_HOME="$PWD/.runtime/cache/huggingface"
export XDG_CACHE_HOME="$PWD/.runtime/cache/xdg"
exec uv run --script scripts/pet.py "$@"
