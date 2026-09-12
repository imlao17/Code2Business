#!/usr/bin/env sh
# Build, Developer-ID sign, notarize and package a macOS release.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DESKTOP_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(CDPATH= cd -- "$DESKTOP_DIR/.." && pwd)"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO_DIR/core/pyproject.toml" | head -n 1)"
ARCH="$(uname -m)"
APP_PATH="$DESKTOP_DIR/dist/C2B Desktop.app"
DIST_DIR="$DESKTOP_DIR/dist"
ARCHIVE_PATH="$DIST_DIR/C2B-Desktop-v$VERSION-macos-$ARCH.zip"
DMG_PATH="$DIST_DIR/C2B-Desktop-v$VERSION-macos-$ARCH.dmg"
SIGNING_IDENTITY="${C2B_MACOS_SIGNING_IDENTITY:-}"
NOTARY_PROFILE="${C2B_NOTARY_KEYCHAIN_PROFILE:-}"
RELEASE_TEMP="$(mktemp -d)"

cleanup() {
  if [ -d "$RELEASE_TEMP" ]; then
    rm -rf "$RELEASE_TEMP"
  fi
}
trap cleanup EXIT INT TERM

if [ -z "$SIGNING_IDENTITY" ]; then
  echo "正式分发需要 C2B_MACOS_SIGNING_IDENTITY。" >&2
  echo "它必须是 Developer ID Application 证书的完整名称。" >&2
  exit 1
fi
case "$SIGNING_IDENTITY" in
  "Developer ID Application:"*) ;;
  *)
    echo "拒绝使用非 Developer ID Application 身份生成公网分发包：$SIGNING_IDENTITY" >&2
    exit 1
    ;;
esac
if [ -z "$NOTARY_PROFILE" ]; then
  echo "正式分发需要 C2B_NOTARY_KEYCHAIN_PROFILE。" >&2
  echo "先用 xcrun notarytool store-credentials 创建钥匙串配置。" >&2
  exit 1
fi
if ! security find-identity -v -p codesigning | grep -Fq "\"$SIGNING_IDENTITY\""; then
  echo "钥匙串中没有找到签名身份：$SIGNING_IDENTITY" >&2
  exit 1
fi

echo "[1/6] 构建并签名应用 ..."
C2B_MACOS_SIGNING_IDENTITY="$SIGNING_IDENTITY" "$SCRIPT_DIR/build-macos.sh"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

echo "[2/6] 提交应用公证 ..."
NOTARY_ZIP="$RELEASE_TEMP/C2B-Desktop-notary.zip"
ditto -c -k --keepParent "$APP_PATH" "$NOTARY_ZIP"
xcrun notarytool submit "$NOTARY_ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$APP_PATH"
xcrun stapler validate "$APP_PATH"

echo "[3/6] 生成已公证 ZIP ..."
rm -f "$ARCHIVE_PATH" "$ARCHIVE_PATH.sha256"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ARCHIVE_PATH"

echo "[4/6] 生成并签名 DMG ..."
DMG_STAGE="$RELEASE_TEMP/dmg"
mkdir -p "$DMG_STAGE"
cp -R "$APP_PATH" "$DMG_STAGE/C2B Desktop.app"
ln -s /Applications "$DMG_STAGE/Applications"
rm -f "$DMG_PATH" "$DMG_PATH.sha256"
hdiutil create -quiet -volname "C2B Desktop" -srcfolder "$DMG_STAGE" -ov -format UDZO "$DMG_PATH"
codesign --force --timestamp --sign "$SIGNING_IDENTITY" "$DMG_PATH"

echo "[5/6] 提交 DMG 公证并装订凭据 ..."
xcrun notarytool submit "$DMG_PATH" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$DMG_PATH"
xcrun stapler validate "$DMG_PATH"

echo "[6/6] Gatekeeper 与校验和检查 ..."
spctl --assess --type execute --verbose=2 "$APP_PATH"
spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG_PATH"
(cd "$DIST_DIR" && shasum -a 256 \
  "$(basename "$ARCHIVE_PATH")" > "$(basename "$ARCHIVE_PATH").sha256")
(cd "$DIST_DIR" && shasum -a 256 \
  "$(basename "$DMG_PATH")" > "$(basename "$DMG_PATH").sha256")

echo ""
echo "正式分发包已通过签名、公证和 Gatekeeper 检查："
echo "$ARCHIVE_PATH"
cat "$ARCHIVE_PATH.sha256"
echo "$DMG_PATH"
cat "$DMG_PATH.sha256"
