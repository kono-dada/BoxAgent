#!/bin/zsh
set -euo pipefail

ROOT_DIR=${0:A:h:h}
VERSION=1.3.0
TARGET_DIR=${BOXAGENT_AOQ_INSTALL_DIR:-"$ROOT_DIR/.runtime/aoq-sdk/$VERSION"}
SDK_URL=https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/files/6a4b3c2d1e0f9d85.zip
OPUS_URL=https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/files/6a4b3c2d1e0f9d84.zip
SDK_SHA256=44c757f0c88c6aeeae725f60aa0ef8ce5bce1621a1f514a93e2f2df3b3b1ba55
OPUS_SHA256=fd1b7f92ae40dd8bb6b1df1adb808b9f52af1ff08458f0189df5ab1fef9f1dbb

if [[ $(uname -s) != Darwin ]]; then
  print -u2 "AOQ macOS Framework 只能在 macOS 上安装。"
  exit 1
fi

temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/boxagent-aoq.XXXXXX")
trap 'rm -rf -- "$temp_dir"' EXIT

download_and_verify() {
  local url=$1
  local expected=$2
  local output=$3
  curl --fail --location --retry 3 --output "$output" "$url"
  local actual
  actual=$(shasum -a 256 "$output" | awk '{print $1}')
  if [[ "$actual" != "$expected" ]]; then
    print -u2 "SHA256 校验失败：$output"
    print -u2 "expected=$expected"
    print -u2 "actual=$actual"
    exit 1
  fi
}

download_and_verify "$SDK_URL" "$SDK_SHA256" "$temp_dir/AoqClientSdk.framework.zip"
download_and_verify "$OPUS_URL" "$OPUS_SHA256" "$temp_dir/PluginOpus.framework.zip"

mkdir -p "$temp_dir/frameworks" "$TARGET_DIR/downloads" "$TARGET_DIR/frameworks"
ditto -x -k "$temp_dir/AoqClientSdk.framework.zip" "$temp_dir/frameworks"
ditto -x -k "$temp_dir/PluginOpus.framework.zip" "$temp_dir/frameworks"

for framework in AoqClientSdk.framework PluginOpus.framework; do
  if [[ ! -d "$temp_dir/frameworks/$framework" ]]; then
    print -u2 "解压后缺少 $framework"
    exit 1
  fi
  rm -rf -- "$TARGET_DIR/frameworks/$framework"
  mv "$temp_dir/frameworks/$framework" "$TARGET_DIR/frameworks/$framework"
done
cp "$temp_dir/AoqClientSdk.framework.zip" "$TARGET_DIR/downloads/"
cp "$temp_dir/PluginOpus.framework.zip" "$TARGET_DIR/downloads/"

print "AOQ SDK $VERSION 已安装到：$TARGET_DIR/frameworks"
print "请在 .env.local 配置 BOXAGENT_DASHSCOPE_WORKSPACE_ID 后重启 BoxAgent。"
