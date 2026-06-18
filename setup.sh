#!/bin/bash
set -e
echo "=== 英語→日本語 自動吹き替えシステム セットアップ (Ubuntu) ==="

# ffmpeg
if ! command -v ffmpeg &>/dev/null; then
  echo "ffmpeg をインストール中..."
  sudo apt update -y && sudo apt install -y ffmpeg
fi
echo "✓ ffmpeg: $(ffmpeg -version 2>&1 | head -1)"

# python3
if ! command -v python3 &>/dev/null; then
  sudo apt install -y python3 python3-pip python3-venv
fi
echo "✓ python3: $(python3 --version)"

# yt-dlp（URL取得オプション用）
if ! command -v yt-dlp &>/dev/null; then
  echo "yt-dlp をインストール中..."
  sudo apt install -y python3-pip
  pip3 install --user yt-dlp
fi
echo "✓ yt-dlp: $(yt-dlp --version 2>/dev/null || echo '未インストール（任意）')"

# 仮想環境
if [ ! -d ".venv" ]; then
  echo "仮想環境を作成中..."
  python3 -m venv .venv
fi
source .venv/bin/activate

pip install --upgrade pip -q
echo "依存ライブラリをインストール中..."
pip install -r requirements.txt -q

# .env ファイルがなければコピー
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "⚠️  .env ファイルを作成しました。ANTHROPIC_API_KEY を設定してください（任意）"
fi

echo ""
echo "✅ セットアップ完了"
echo "起動: bash run.sh"
echo ""
echo "--- 設定ヒント ---"
echo " ・高精度翻訳: .env の ANTHROPIC_API_KEY を設定してください"
echo " ・無料翻訳: TRANSLATION_BACKEND=google のまま使用できます"
echo " ・Whisper精度向上: WHISPER_MODEL=large-v3 （メモリ10GB必要）"
