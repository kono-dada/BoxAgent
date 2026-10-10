#!/usr/bin/env bash
# 构建本地渲染器并导入用户提供的企鹅素材。
set -euo pipefail
cd "$(dirname "$0")/.."
pnpm --dir web/vrm install --frozen-lockfile
pnpm --dir web/vrm build
node scripts/import_penguin.mjs "${1:-$HOME/Downloads/vrm_asset}"
printf '%s\n' '准备完成。启动桌宠后，在右键菜单选择「本地 VRM 形象 → 小企鹅」。'
