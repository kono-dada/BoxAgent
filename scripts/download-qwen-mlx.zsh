#!/bin/zsh
set -euo pipefail

# 从 ModelScope 下载已转换的 MLX 模型，并校验每个文件。
repo_root="${0:A:h:h}"
model_dir="$repo_root/models/qwen3.5-0.8b-mlx"
model_url='https://modelscope.cn/models/mlx-community/Qwen3.5-0.8B-4bit/resolve/master'
file_api='https://modelscope.cn/api/v1/models/mlx-community/Qwen3.5-0.8B-4bit/repo/files?Revision=master'
mkdir -p "$model_dir"

curl -fsSL --retry 3 "$file_api" |
  jq -er '.Data.Files[] | select(.Type == "blob") | [.Path, .Sha256] | @tsv' > "$model_dir/manifest.tsv"

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
  curl -fL --retry 3 --retry-delay 2 -C - -o "$target" "$model_url/$file_path"
  actual_hash="$(shasum -a 256 "$target" | awk '{print $1}')"
  if [[ "$actual_hash" != "$expected_hash" ]]; then
    print -u2 "校验失败：$file_path"
    exit 1
  fi
done < "$model_dir/manifest.tsv"
