#!/bin/bash
INSTALL_DIR="$HOME/voicevox_engine"

if [ ! -f "$INSTALL_DIR/run" ]; then
  echo "❌ VOICEVOXエンジンが見つかりません。先に bash setup_voicevox.sh を実行してください。"
  exit 1
fi

echo "🎙️ VOICEVOX エンジンを起動中... (http://localhost:50021)"
echo "   停止: Ctrl+C"
echo "   スピーカー一覧: http://localhost:50021/speakers"
echo ""

"$INSTALL_DIR/run" --host 0.0.0.0 --port 50021
