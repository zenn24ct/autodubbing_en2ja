#!/bin/bash
set -e

if [ ! -d ".venv" ]; then
  echo "❌ 仮想環境が見つかりません。先に bash setup.sh を実行してください。"
  exit 1
fi

source .venv/bin/activate

# .env 読み込み
if [ -f ".env" ]; then
  export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi

PORT=${PORT:-8000}

echo "🚀 英語→日本語 自動吹き替えシステム"
echo "   http://localhost:${PORT} で起動中..."
echo "   停止: Ctrl+C"
echo ""

uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload
