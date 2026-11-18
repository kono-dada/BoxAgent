#!/bin/bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"
identity="${BOXAGENT_SIGN_IDENTITY:--}"
wechat_source="${BOXAGENT_WECHAT_SOURCE:-$repo_root/.runtime/package-resources/wechat/4.1.15.270115.dmg}"
if [[ ! -e "$wechat_source" ]]; then wechat_source='/Applications/WeChat.app'; fi
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo 'Build on Apple Silicon macOS.' >&2; exit 1
fi
if [[ "${1:-}" != '--reuse-resources' ]]; then
  pnpm --dir web/vrm build
  uv run --python 3.12.9 --script scripts/prepare-packaged-runtime.py \
    --aoq-dir "${BOXAGENT_AOQ_SDK_DIR:?Set BOXAGENT_AOQ_SDK_DIR to the official frameworks directory}" \
    --wechat-source "$wechat_source" \
    --sign-identity "$identity"
else
  if [[ "$identity" != '-' ]]; then
    recorded_identity="$(node -p 'require("./.runtime/package-resources/build-identity.json").wheelSigningIdentity')"
    if [[ "$recorded_identity" != "$identity" ]]; then
      echo 'Signing identity changed. Run a full resource build before signing.' >&2; exit 1
    fi
  fi
  uv run --python 3.12.9 --script scripts/prepare-packaged-runtime.py --refresh-source "$repo_root/.runtime/package-resources" \
    --wechat-source "$wechat_source"
fi
pnpm --dir web/panel build
CSC_IDENTITY_AUTO_DISCOVERY=false pnpm --dir web/panel exec electron-builder --config electron-builder.cjs --config.directories.output=../../.runtime/package-build --mac --arm64 --dir
app_bundle="$repo_root/.runtime/package-build/mac-arm64/BoxAgent.app"
entitlements="$repo_root/web/panel/packaging/entitlements.mac.plist"
while IFS= read -r -d '' candidate; do
  if file -b "$candidate" | /usr/bin/grep -q 'Mach-O'; then
    args=(--force --sign "$identity")
    if [[ "$identity" != '-' ]]; then args+=(--options runtime --timestamp); fi
    codesign "${args[@]}" --entitlements "$entitlements" "$candidate"
  fi
done < <(find "$app_bundle/Contents" -type f -print0)
# Seal nested bundles from the inside out before hashing their final resource files.
while IFS= read -r -d '' nested; do
  args=(--force --sign "$identity")
  if [[ "$identity" != '-' ]]; then args+=(--options runtime --timestamp); fi
  codesign "${args[@]}" --entitlements "$entitlements" "$nested"
done < <(find "$app_bundle/Contents" -depth -type d \( -name '*.framework' -o -name '*.app' -o -name '*.xpc' \) -print0)
# The manifest must identify the actual signed resources delivered to the user.
uv run --python 3.12.9 --script scripts/prepare-packaged-runtime.py --refresh-manifest "$app_bundle/Contents/Resources"
args=(--force --sign "$identity")
if [[ "$identity" != '-' ]]; then args+=(--options runtime --timestamp); fi
codesign "${args[@]}" --entitlements "$entitlements" "$app_bundle"
codesign --verify --deep --strict "$app_bundle"
node scripts/verify-packaged-app.mjs "$app_bundle"
if [[ "$identity" != '-' ]]; then
  ditto -c -k --keepParent "$app_bundle" "$repo_root/dist/mac/BoxAgent-notary.zip"
  xcrun notarytool submit "$repo_root/dist/mac/BoxAgent-notary.zip" --keychain-profile "${BOXAGENT_NOTARY_PROFILE:?Set BOXAGENT_NOTARY_PROFILE}" --wait
  xcrun stapler staple "$app_bundle"
fi
version="$(node -p 'require("./web/panel/package.json").version')"
artifact="$repo_root/dist/mac/BoxAgent-${version}-macos-arm64-setup.dmg"
node scripts/build-dmg-mac.cjs "$app_bundle" "$artifact"
if [[ "$identity" != '-' ]]; then
  codesign --sign "$identity" --timestamp "$artifact"
  xcrun notarytool submit "$artifact" --keychain-profile "$BOXAGENT_NOTARY_PROFILE" --wait
  xcrun stapler staple "$artifact"
  xcrun stapler validate "$artifact"
  spctl --assess --verbose "$app_bundle"
fi
shasum -a 256 "$artifact" >"$artifact.sha256"
echo "Built $artifact (signing identity: $identity)"
