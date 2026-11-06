# Android 壳构建器（ADR-0042）：JDK + Android build-tools 直出 APK，不装 Gradle/AGP。
# 首次构建需要网络下载 Android SDK；之后 build.sh 在 --network none 下也能跑。
# 基础镜像用仓库已有的 maven:3.9-eclipse-temurin-17（Ubuntu 24.04 + JDK 17），避免再拉一套 JDK。
FROM maven:3.9-eclipse-temurin-17

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      curl unzip zip imagemagick librsvg2-bin fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*

ENV ANDROID_SDK_ROOT=/opt/android-sdk \
    ANDROID_HOME=/opt/android-sdk

RUN mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools" \
 && curl -fsSL https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip -o /tmp/tools.zip \
 && unzip -q /tmp/tools.zip -d "$ANDROID_SDK_ROOT/cmdline-tools" \
 && mv "$ANDROID_SDK_ROOT/cmdline-tools/cmdline-tools" "$ANDROID_SDK_ROOT/cmdline-tools/latest" \
 && rm /tmp/tools.zip

RUN yes | "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$ANDROID_SDK_ROOT" --licenses >/dev/null \
 && "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$ANDROID_SDK_ROOT" \
      "platforms;android-34" "build-tools;34.0.0" >/dev/null

ENV PATH="$ANDROID_SDK_ROOT/build-tools/34.0.0:$ANDROID_SDK_ROOT/platform-tools:$PATH"

WORKDIR /workspace
CMD ["/bin/sh"]
