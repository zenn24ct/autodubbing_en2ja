#!/bin/bash
set -e
echo "=== VOICEVOX エンジン セットアップ (Ubuntu) ==="
echo "VOICEVOXエンジン（GUIなし・APIサーバー版）をインストールします"
echo ""

# インストール先
INSTALL_DIR="$HOME/voicevox_engine"
VERSION="0.22.1"  # 安定版

# すでにインストール済みか確認
if [ -f "$INSTALL_DIR/run" ]; then
  echo "✅ VOICEVOX エンジンは既にインストール済みです: $INSTALL_DIR"
  echo "起動: bash start_voicevox.sh"
  exit 0
fi

echo "ダウンロード中... (約500MB)"
mkdir -p "$INSTALL_DIR"

# CPU版をダウンロード（GPUなし環境向け）
DOWNLOAD_URL="https://github.com/VOICEVOX/voicevox_engine/releases/download/${VERSION}/voicevox_engine-linux-cpu-${VERSION}.7z.001"

if ! command -v wget &>/dev/null; then
  sudo apt install -y wget
fi

# 分割ファイルをダウンロード
cd /tmp
wget -c "${DOWNLOAD_URL}" -O voicevox_engine.7z.001

# 複数ファイルある場合は全部ダウンロード
for i in 002 003 004 005; do
  URL="https://github.com/VOICEVOX/voicevox_engine/releases/download/${VERSION}/voicevox_engine-linux-cpu-${VERSION}.7z.${i}"
  if wget --spider "$URL" 2>/dev/null; then
    wget -c "$URL" -O "voicevox_engine.7z.${i}"
  else
    break
  fi
done

# 展開
if ! command -v 7z &>/dev/null; then
  sudo apt install -y p7zip-full
fi

7z x voicevox_engine.7z.001 -o"$INSTALL_DIR" -y
rm -f /tmp/voicevox_engine.7z.*

chmod +x "$INSTALL_DIR/run"

echo ""
echo "✅ VOICEVOX エンジンのセットアップ完了: $INSTALL_DIR"
echo ""
echo "--- 使い方 ---"
echo "1. VOICEVOXを起動:  bash start_voicevox.sh"
echo "2. .envを編集:      TTS_BACKEND=voicevox"
echo "3. 吹き替えシステム起動: bash run.sh"
