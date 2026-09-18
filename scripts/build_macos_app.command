#!/bin/zsh
set -euo pipefail

cd "${0:A:h}/.."

PYTHON="${PWD}/.venv/bin/python"
PIP="${PWD}/.venv/bin/pip"
ICON_SOURCE="${PWD}/assets/app-icon.svg"
ICONSET="${PWD}/build/FDUCourseHelper.iconset"
ICON_BASE="${PWD}/build/FDUCourseHelper-1024.png"
APP_PATH="${PWD}/dist/FDU选课助手.app"
ZIP_PATH="${PWD}/dist/FDU选课助手-macOS-arm64.zip"
DMG_PATH="${PWD}/dist/FDU选课助手-macOS-arm64.dmg"
DMG_STAGE="${PWD}/build/dmg-stage"

if [ ! -x "$PYTHON" ]; then
  python3 -m venv .venv
fi

"$PIP" install -r requirements-build.txt

rm -rf "${PWD}/build/FDUCourseHelper" "$ICONSET" "$DMG_STAGE"
rm -rf "$APP_PATH"
rm -f "$ZIP_PATH" "$DMG_PATH" "$ICON_BASE" "${PWD}/build/FDUCourseHelper.icns"
mkdir -p "$ICONSET" "$DMG_STAGE"

sips -s format png "$ICON_SOURCE" --out "$ICON_BASE" >/dev/null
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ICON_BASE" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$ICON_BASE" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "${PWD}/build/FDUCourseHelper.icns"

"$PYTHON" -m PyInstaller --noconfirm --clean FDUCourseHelper.spec
codesign --force --deep --sign - "$APP_PATH"
codesign --verify --deep --strict "$APP_PATH"

ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"
ditto "$APP_PATH" "$DMG_STAGE/FDU选课助手.app"
ln -s /Applications "$DMG_STAGE/Applications"
hdiutil create -volname "FDU选课助手" -srcfolder "$DMG_STAGE" -ov -format UDZO "$DMG_PATH" >/dev/null

echo ""
echo "构建完成："
echo "  $APP_PATH"
echo "  $ZIP_PATH"
echo "  $DMG_PATH"
