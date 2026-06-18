# 英語→日本語 自動吹き替えシステム

英語の動画・音声ファイルを入力すると、日本語吹き替え済み動画を自動生成するローカル実行システムです。

## 機能

- 英語音声の文字起こし（OpenAI Whisper）
- 英語→日本語 翻訳（Claude API または Google翻訳）
- 日本語音声の生成（edge-tts: Microsoft Neural TTS）
- 元動画の音声を日本語に差し替えて出力
- 字幕ファイル（SRT）の自動生成
- 翻訳結果のブラウザ上での確認・編集

## 必要環境

- Ubuntu 20.04 以上
- Python 3.10 以上
- ffmpeg
- インターネット接続（edge-tts, 翻訳API）

## セットアップ

```bash
git clone <このリポジトリ>
cd autodubbing_en2ja
bash setup.sh
```

`.env` ファイルを編集して API キーを設定（Claude翻訳を使う場合）:

```bash
cp .env.example .env
nano .env  # ANTHROPIC_API_KEY を設定
```

## 起動

```bash
bash run.sh
```

ブラウザで `http://localhost:8000` を開きます。

## 使い方

1. **ファイルをアップロード**（または URL を入力）
2. Whisper モデルを選択して「アップロード & 文字起こし開始」
3. 処理完了後、「翻訳結果を確認・編集」で内容を確認・修正
4. 声優を選択して「日本語音声を合成して動画を生成」
5. 完成したら動画・音声・字幕をダウンロード

## 設定（.env）

| 変数 | 説明 | デフォルト |
|------|------|-----------|
| `ANTHROPIC_API_KEY` | Claude API キー（高品質翻訳） | 未設定 |
| `WHISPER_MODEL` | Whisper モデルサイズ | `medium` |
| `TRANSLATION_BACKEND` | `claude` または `google` | `claude` |
| `DEFAULT_VOICE` | `female` または `male` | `female` |
| `PORT` | サーバーポート | `8000` |

## 翻訳バックエンドの選択

| バックエンド | 品質 | 速度 | コスト |
|------------|------|------|--------|
| `claude`   | ★★★ 高品質・文脈考慮 | 中 | API利用料 |
| `google`   | ★★☆ 標準 | 速い | 無料 |

`ANTHROPIC_API_KEY` が未設定の場合は自動的に `google` にフォールバックします。

## 処理フロー

```
[入力ファイル]
    ↓ ffmpeg 音声抽出
[Whisper 英語文字起こし]
    ↓ セグメント統合
[英語テキスト] → segments_en.json
    ↓ 翻訳（Claude or Google）
[日本語テキスト] → segments_ja.json
    ↓ (任意) ブラウザ上で編集
[edge-tts 音声合成]
    ↓ 話速調整（セグメント尺に合わせる）
[日本語音声トラック]
    ↓ ffmpeg 動画合成
[出力: output.mp4, subtitle.srt, japanese_audio.wav]
```

## ディレクトリ構成

```
autodubbing_en2ja/
├── app/
│   ├── main.py        # FastAPI エンドポイント
│   ├── pipeline.py    # 処理パイプライン
│   └── static/
│       ├── index.html # メインUI
│       └── edit.html  # 翻訳編集UI
├── jobs/              # ジョブデータ（自動生成）
│   └── <job_id>/
│       ├── original.*          # 入力ファイル
│       ├── segments_en.json    # 英語文字起こし
│       ├── segments_ja.json    # 日本語翻訳
│       ├── segments_ja_edited.json  # 編集済み（あれば優先）
│       ├── japanese_audio.wav  # 日本語音声
│       ├── subtitle.srt        # 字幕
│       ├── output.mp4          # 完成動画
│       └── status.json         # 処理状態
├── .env.example
├── .gitignore
├── requirements.txt
├── run.sh
└── setup.sh
```

## 置き換えポイント

- **Whisper の代替**: `faster-whisper` に変えると GPU 環境で数倍高速化できます
- **翻訳の代替**: `pipeline.py` の `translate_text_*` 関数を差し替えるだけで DeepL 等に対応可能
- **TTS の代替**: VOICEVOX（ローカル・高品質）に差し替える場合は `tts_segment_sync` を置き換えてください
- **話速調整の無効化**: `adjust_speed` の呼び出しをコメントアウトすると元のTTS速度のまま出力されます

## 動作確認手順

```bash
# 1. セットアップ
bash setup.sh

# 2. .env 設定（任意）
cp .env.example .env

# 3. 起動
bash run.sh

# 4. ブラウザで確認
open http://localhost:8000

# 5. テスト用短い英語動画（数十秒）でアップロード→処理→ダウンロードを確認
```

## 注意事項

- Whisper の初回実行時はモデルダウンロードが発生します（medium: 約1.5GB）
- `large-v3` モデルは精度が高いですが、VRAM/RAM が 10GB 以上必要です
- edge-tts はインターネット接続が必要です（完全オフラインには VOICEVOX 等が必要）
- 長い動画（1時間以上）は処理に時間がかかります。進捗はブラウザ画面で確認できます
