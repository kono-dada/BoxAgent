#!/bin/zsh
set -euo pipefail

# 用一张本地图片进行一次推理，不启动常驻服务。
repo_root="${0:A:h:h}"
if [[ $# -lt 1 || ! -f "$1" ]]; then
  print -u2 "用法：zsh scripts/probe-qwen-mlx.zsh 图片路径 [问题]"
  exit 2
fi
image_file="${1:A}"
prompt="${2:-只根据这张截图，用中文说明当前页面的主要内容，并列出可见依据。看不清的内容不要猜。}"

cd "$repo_root"
uv run --no-project --python .venv/bin/python -m mlx_vlm.generate \
  --model models/qwen3.5-0.8b-mlx \
  --image "$image_file" \
  --prompt "$prompt" \
  --max-tokens 180 \
  --temperature 0.0 \
  --verbose
