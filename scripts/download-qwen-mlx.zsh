#!/bin/zsh
set -euo pipefail

# 从 ModelScope 下载已转换的 MLX 模型，并校验每个文件。
repo_root="${0:A:h:h}"
BOXAGENT_ROOT=$repo_root
source "$repo_root/scripts/runtime-env.sh"
model_dir="$BOXAGENT_PERCEPTION_MODEL"
model_url='https://modelscope.cn/models/mlx-community/Qwen3.5-0.8B-4bit/resolve/master'
mkdir -p "$model_dir"
cp "$repo_root/scripts/model-manifests/qwen3.5-0.8b-mlx.manifest.tsv" "$model_dir/manifest.tsv"

while IFS=$'\t' read -r file_path expected_hash; do
  target="$model_dir/$file_path"
  if [[ -f "$target" ]]; then
    actual_hash="$(shasum -a 256 "$target" | awk '{print $1}')"
    if [[ "$actual_hash" == "$expected_hash" ]]; then
      print "已校验：$file_path"
      continue
    fi
  fi

  print "下载：$file_path"
  mkdir -p "${target:h}"
  curl -fL --retry 3 --retry-delay 2 -o "$target.part" "$model_url/$file_path"
  actual_hash="$(shasum -a 256 "$target.part" | awk '{print $1}')"
  if [[ "$actual_hash" != "$expected_hash" ]]; then
    print -u2 "校验失败：$file_path"
    exit 1
  fi
  mv "$target.part" "$target"
done < "$model_dir/manifest.tsv"
