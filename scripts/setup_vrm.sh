#!/usr/bin/env bash
# 团队首次拉取后，下载 LFS 资产并构建统一播放器。
set -euo pipefail
cd "$(dirname "$0")/.."
git lfs install --local
git lfs pull
pnpm --dir web/vrm install --frozen-lockfile
pnpm --dir web/vrm build
printf '%s\n' '3D 资源已准备完成，可以运行 ./启动桌宠.command。'
