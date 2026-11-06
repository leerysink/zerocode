#!/bin/sh
# 在构建器容器内把一个最小 WebView 壳打成签名 APK（ADR-0042）。
# 输入：/workspace 下的 AndroidManifest.xml、res/、src/、icon.png 或 icon.svg、
#       keystore/pass.txt（平台已生成）；输出：/workspace/out/app.apk。
set -eu

OUT=/workspace/out
BUILD=/workspace/build
SDK="${ANDROID_SDK_ROOT:-/opt/android-sdk}"
BT="$SDK/build-tools/34.0.0"
PLATFORM="$SDK/platforms/android-34/android.jar"

: "${APP_VERSION_CODE:?APP_VERSION_CODE required}"
: "${APP_VERSION_NAME:?APP_VERSION_NAME required}"
[ -f "$PLATFORM" ] || { echo "android.jar missing: $PLATFORM" >&2; exit 3; }
[ -f "$BT/aapt2" ] || { echo "build-tools missing: $BT" >&2; exit 3; }

rm -rf "$BUILD" "$OUT"
mkdir -p "$BUILD/classes" "$BUILD/dex" "$OUT"

# ---- 图标：自定义 PNG 优先，否则把自动 SVG 栅格化；生成五档 mipmap ----
ICON_MASTER="$BUILD/icon.png"
if [ -f /workspace/icon.png ]; then
  convert /workspace/icon.png -auto-orient -resize '512x512^' -gravity center \
    -extent 512x512 "$ICON_MASTER"
else
  if ! rsvg-convert -w 512 -h 512 /workspace/icon.svg -o "$ICON_MASTER" 2>/dev/null; then
    convert -size 512x512 xc:'#1d4eff' "$ICON_MASTER"
  fi
fi
for pair in mdpi:48 hdpi:72 xhdpi:96 xxhdpi:144 xxxhdpi:192; do
  density=${pair%%:*}
  size=${pair##*:}
  mkdir -p "/workspace/res/mipmap-$density"
  convert "$ICON_MASTER" -resize "${size}x${size}" "/workspace/res/mipmap-$density/ic_launcher.png"
done

# ---- 编译 Java + dex ----
find /workspace/src -name '*.java' > "$BUILD/sources.txt"
javac -encoding UTF-8 -source 8 -target 8 -bootclasspath "$PLATFORM" \
  -d "$BUILD/classes" @"$BUILD/sources.txt"
"$BT/d8" --lib "$PLATFORM" --min-api 24 --output "$BUILD/dex" \
  $(find "$BUILD/classes" -name '*.class')

# ---- 资源 + Manifest + dex 合成 APK ----
"$BT/aapt2" compile --dir /workspace/res -o "$BUILD/res.zip"
"$BT/aapt2" link -o "$BUILD/base.apk" -I "$PLATFORM" \
  --manifest /workspace/AndroidManifest.xml \
  --min-sdk-version 24 --target-sdk-version 34 \
  --version-code "$APP_VERSION_CODE" --version-name "$APP_VERSION_NAME" \
  "$BUILD/res.zip"
cp "$BUILD/dex/classes.dex" "$BUILD/classes.dex"
( cd "$BUILD" && zip -q base.apk classes.dex )
"$BT/zipalign" -f 4 "$BUILD/base.apk" "$BUILD/aligned.apk"

# ---- 签名：平台统一 keystore，缺失时用平台给的口令生成一次 ----
KS_DIR=/workspace/keystore
KS="$KS_DIR/zerocode.jks"
PASS_FILE="$KS_DIR/pass.txt"
[ -f "$PASS_FILE" ] || { echo "keystore password missing: $PASS_FILE" >&2; exit 3; }
PASS=$(head -n 1 "$PASS_FILE")
# apksigner 对「同一个密码文件」按行依次取 ks/key 两个口令，单行文件第二次必读空。
# 复制成两个文件，各自都是单行。
printf '%s\n' "$PASS" > "$BUILD/key-pass.txt"
if [ ! -f "$KS" ]; then
  keytool -genkeypair -keystore "$KS" -alias zerocode -keyalg RSA -keysize 2048 \
    -validity 10000 -storepass "$PASS" -keypass "$PASS" \
    -dname "CN=ZeroCode Mobile, O=ZeroCode, C=CN" >/dev/null 2>&1
fi

"$BT/apksigner" sign --ks "$KS" --ks-key-alias zerocode \
  --ks-pass "file:$PASS_FILE" --key-pass "file:$BUILD/key-pass.txt" \
  --out "$OUT/app.apk" "$BUILD/aligned.apk"
"$BT/apksigner" verify "$OUT/app.apk"

echo "APK OK: $(wc -c < "$OUT/app.apk") bytes"
