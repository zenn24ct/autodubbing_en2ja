"""
処理パイプライン — 英語→日本語 自動吹き替え

STEP 1  : 音声抽出 + Whisper 英語文字起こし → segments_en.json
STEP 1.5: (任意) Claude による英語テキスト整形
STEP 2  : 英語→日本語 翻訳 → segments_ja.json
STEP 3  : edge-tts で日本語音声生成 + 話速調整
STEP 4  : 日本語音声トラック合成 → 動画に差し替え
STEP 5  : (任意) SRT 字幕ファイル生成
"""

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

import whisper
import edge_tts
from deep_translator import GoogleTranslator
from pydub import AudioSegment

JOBS_DIR = Path("jobs")

# ── TTS バックエンド設定 ─────────────────────────────────────────────
# TTS_BACKEND=edge    : edge-tts（デフォルト・ネット必要）
# TTS_BACKEND=voicevox: VOICEVOX（ローカル・高品質・要VOICEVOX起動）
TTS_BACKEND = os.environ.get("TTS_BACKEND", "edge").lower()

# edge-tts 音声
EDGE_VOICES = {
    "female": "ja-JP-NanamiNeural",
    "male":   "ja-JP-KeitaNeural",
}

# VOICEVOX スピーカーID
# https://voicevox.hiroshiba.jp/ でキャラ一覧確認
VOICEVOX_SPEAKERS = {
    "female": 3,   # ずんだもん（ノーマル）
    "male":   2,   # 四国めたん（ノーマル）
}
VOICEVOX_URL = os.environ.get("VOICEVOX_URL", "http://localhost:50021")


# ── ステータス管理 ────────────────────────────────────────────────────
def update_status(job_id: str, status: str, progress: int, message: str) -> None:
    path = JOBS_DIR / job_id / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"status": status, "progress": progress, "message": message},
            f, ensure_ascii=False,
        )
    print(f"[{job_id}] [{progress:3d}%] {message}")


# ── 音声抽出 ─────────────────────────────────────────────────────────
def extract_audio(input_path: str, audio_path: str) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", input_path,
         "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
         audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"音声抽出エラー: {result.stderr[-500:]}")


# ── 動画の長さ取得 ────────────────────────────────────────────────────
def get_duration(file_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", file_path],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


# ── 英語セグメントを文単位に統合 ─────────────────────────────────────
_SENTENCE_FINAL = frozenset('.!?…')
_MAX_SEG_SEC    = 15.0


def merge_into_sentences(segments: list[dict]) -> list[dict]:
    """
    Whisper の短いセグメントを文末（. ! ?）または最大秒数で区切って統合する。
    """
    if not segments:
        return segments

    merged: list[dict] = []
    buf_text  = ""
    buf_start = segments[0]["start"]
    buf_end   = segments[0]["end"]

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue

        if not buf_text:
            buf_start = seg["start"]

        buf_text += (" " if buf_text else "") + text
        buf_end   = seg["end"]
        duration  = buf_end - buf_start

        ends_sentence = buf_text.rstrip()[-1:] in _SENTENCE_FINAL if buf_text.rstrip() else False
        too_long      = duration >= _MAX_SEG_SEC

        if ends_sentence or too_long:
            merged.append({
                "start": round(buf_start, 2),
                "end":   round(buf_end,   2),
                "text":  buf_text.strip(),
            })
            buf_text = ""

    if buf_text.strip():
        merged.append({
            "start": round(buf_start, 2),
            "end":   round(buf_end,   2),
            "text":  buf_text.strip(),
        })

    return merged


# ── STEP 1: Whisper 英語文字起こし ───────────────────────────────────
def run_transcription(job_id: str, input_path: str, model_size: str = "medium") -> None:
    try:
        update_status(job_id, "transcribing", 5, "音声を抽出中...")

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_wav = os.path.join(tmpdir, "audio.wav")
            extract_audio(input_path, audio_wav)

            update_status(job_id, "transcribing", 15,
                          f"Whisper ({model_size}) で英語を文字起こし中...（数分かかります）")

            model_size_env = os.environ.get("WHISPER_MODEL", model_size)
            model = whisper.load_model(model_size_env)
            result = model.transcribe(
                audio_wav,
                language="en",
                task="transcribe",
                verbose=False,
                initial_prompt=(
                    "The following is English speech. "
                    "Please transcribe it accurately with proper punctuation."
                ),
            )

        raw_segments = [
            {
                "start": round(s["start"], 2),
                "end":   round(s["end"],   2),
                "text":  s["text"].strip(),
            }
            for s in result["segments"]
            if s["text"].strip()
        ]
        segments = merge_into_sentences(raw_segments)

        out = JOBS_DIR / job_id / "segments_en.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)

        update_status(job_id, "translating", 50,
                      f"文字起こし完了（{len(segments)} セグメント）。翻訳中...")

        # 文字起こし後すぐ翻訳まで実行
        segments_ja = translate_segments(job_id, segments)

        out_ja = JOBS_DIR / job_id / "segments_ja.json"
        with open(out_ja, "w", encoding="utf-8") as f:
            json.dump(segments_ja, f, ensure_ascii=False, indent=2)

        update_status(job_id, "ready_to_edit", 100,
                      f"翻訳完了（{len(segments_ja)} セグメント）。編集・確認できます。")

    except Exception as e:
        update_status(job_id, "error", 0, f"文字起こしエラー: {e}")
        raise


# ── STEP 2: 英語→日本語 翻訳 ────────────────────────────────────────
def translate_text_google(text: str) -> str:
    try:
        return GoogleTranslator(source="en", target="ja").translate(text)
    except Exception as e:
        print(f"[Google翻訳エラー] {e}")
        return text


def translate_text_claude(text: str, client) -> str:
    """Claude API を使って英語→日本語翻訳（文脈を考慮した高品質訳）。"""
    try:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": (
                    "以下の英語テキストを日本語に翻訳してください。\n"
                    "・吹き替え音声として使用するため、自然な話し言葉調で訳してください\n"
                    "・原文のニュアンスと口調（カジュアル/フォーマル）を保つこと\n"
                    "・翻訳結果のみを出力し、説明は不要です\n\n"
                    f"{text}"
                ),
            }],
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"[Claude翻訳エラー、Google翻訳にフォールバック] {e}")
        return translate_text_google(text)


def translate_segments(job_id: str, segments: list[dict]) -> list[dict]:
    """全セグメントを翻訳して日本語セグメントリストを返す。"""
    backend = os.environ.get("TRANSLATION_BACKEND", "google").lower()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    use_claude = backend == "claude" and bool(api_key)

    claude_client = None
    if use_claude:
        import anthropic
        claude_client = anthropic.Anthropic(api_key=api_key)
        print(f"[{job_id}] 翻訳バックエンド: Claude API")
    else:
        print(f"[{job_id}] 翻訳バックエンド: Google翻訳（deep-translator）")

    ja_segments = []
    total = len(segments)

    for i, seg in enumerate(segments):
        en_text = seg["text"].strip()
        if not en_text:
            ja_segments.append({**seg, "text": ""})
            continue

        if use_claude:
            ja_text = translate_text_claude(en_text, claude_client)
        else:
            ja_text = translate_text_google(en_text)

        ja_segments.append({
            "start": seg["start"],
            "end":   seg["end"],
            "text":  ja_text,
        })

        progress = int(50 + (i + 1) / total * 45)
        update_status(job_id, "translating", progress,
                      f"翻訳中 ({i + 1}/{total}): {en_text[:30]}...")

    return ja_segments


# ── STEP 3: TTS（edge-tts） ──────────────────────────────────────────
async def _edge_tts_async(text: str, output_path: str, voice: str) -> None:
    await edge_tts.Communicate(text, voice).save(output_path)


def _tts_edge(text: str, output_path: str, voice_key: str) -> None:
    voice = EDGE_VOICES.get(voice_key, EDGE_VOICES["female"])
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_edge_tts_async(text, output_path, voice))
    finally:
        loop.close()


# ── STEP 3: TTS（VOICEVOX） ──────────────────────────────────────────
def _tts_voicevox(text: str, output_path: str, voice_key: str) -> None:
    import urllib.request, urllib.parse
    speaker_id = VOICEVOX_SPEAKERS.get(voice_key, VOICEVOX_SPEAKERS["female"])

    # audio_query
    query_url = f"{VOICEVOX_URL}/audio_query?text={urllib.parse.quote(text)}&speaker={speaker_id}"
    req = urllib.request.Request(query_url, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        query = json.loads(resp.read())

    # speedScale を少し上げて自然なテンポに（デフォルト1.0）
    query["speedScale"] = float(os.environ.get("VOICEVOX_SPEED", "1.1"))
    query["intonationScale"] = 1.1  # 抑揚を少し強調

    # synthesis
    body = json.dumps(query).encode()
    synth_url = f"{VOICEVOX_URL}/synthesis?speaker={speaker_id}"
    req2 = urllib.request.Request(synth_url, data=body, method="POST",
                                   headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req2, timeout=60) as resp:
        wav_data = resp.read()

    with open(output_path, "wb") as f:
        f.write(wav_data)


def tts_segment_sync(text: str, output_path: str, voice_key: str) -> None:
    """バックエンドに応じてTTSを呼び分ける。"""
    if TTS_BACKEND == "voicevox":
        # VOICEVOXはWAV出力なので拡張子をwavに変える
        wav_path = output_path.replace(".mp3", ".wav")
        _tts_voicevox(text, wav_path, voice_key)
        # pydubで読めるようにmp3に変換
        AudioSegment.from_wav(wav_path).export(output_path, format="mp3")
        os.unlink(wav_path)
    else:
        _tts_edge(text, output_path, voice_key)


# ── 音声速度調整 ──────────────────────────────────────────────────────
# 方針: 最大1.4倍速までしか圧縮しない。それ以上伸びる場合はそのまま流す
# （無理に圧縮するより、少し重なる方が自然に聞こえる）
_MAX_SPEED = 1.4


def _build_atempo(speed: float) -> str:
    parts: list[str] = []
    r = speed
    while r > 2.0:
        parts.append("atempo=2.0")
        r /= 2.0
    while r < 0.5:
        parts.append("atempo=0.5")
        r *= 2.0
    parts.append(f"atempo={r:.4f}")
    return ",".join(parts)


def adjust_speed(audio: AudioSegment, target_ms: float) -> AudioSegment:
    current_ms = len(audio)
    if current_ms == 0 or target_ms <= 0:
        return audio

    speed = current_ms / target_ms

    # 上限を1.4倍に設定。それ以上必要な場合は調整しない（流す）
    if speed > _MAX_SPEED:
        print(f"[速度調整スキップ] 必要速度 {speed:.2f}x > 上限 {_MAX_SPEED}x → そのまま流す")
        return audio

    # 1.05倍以内なら調整不要
    if speed < 1.05:
        return audio

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_in = f.name
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_out = f.name
    try:
        audio.export(tmp_in, format="mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_in,
             "-filter:a", _build_atempo(speed), tmp_out],
            check=True, capture_output=True,
        )
        return AudioSegment.from_mp3(tmp_out)
    finally:
        os.unlink(tmp_in)
        os.unlink(tmp_out)


# ── STEP 5: SRT 字幕生成 ─────────────────────────────────────────────
def _sec_to_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(segments: list[dict], output_path: str) -> None:
    lines = []
    for i, seg in enumerate(segments, 1):
        if not seg.get("text", "").strip():
            continue
        start = _sec_to_srt_time(seg["start"])
        end   = _sec_to_srt_time(seg["end"])
        lines.append(f"{i}\n{start} --> {end}\n{seg['text'].strip()}\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ── フルパイプライン（TTS → 動画合成 → 字幕） ───────────────────────
def run_pipeline(job_id: str, voice_key: str = "female", make_subtitle: bool = True) -> None:
    try:
        job_dir = JOBS_DIR / job_id
        voice   = voice_key  # tts_segment_sync に voice_key をそのまま渡す

        # 編集済み JSON を優先
        edited   = job_dir / "segments_ja_edited.json"
        original = job_dir / "segments_ja.json"
        seg_path = edited if edited.exists() else original

        if not seg_path.exists():
            raise RuntimeError("日本語セグメントファイルが見つかりません")

        with open(seg_path, encoding="utf-8") as f:
            ja_segments = json.load(f)

        # 動画ファイルを検索（音声ファイルも許容）
        input_files = [
            p for p in job_dir.iterdir()
            if p.stem == "original"
            and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".mp3", ".wav", ".m4a"}
        ]
        if not input_files:
            raise RuntimeError("元のファイルが見つかりません")
        input_path     = str(input_files[0])
        is_audio_only  = input_files[0].suffix.lower() in {".mp3", ".wav", ".m4a"}
        total_duration = get_duration(input_path)
        total          = len(ja_segments)

        # ── 字幕生成 ────────────────────────────────────────────────
        if make_subtitle:
            update_status(job_id, "processing", 2, "字幕ファイルを生成中...")
            generate_srt(ja_segments, str(job_dir / "subtitle.srt"))

        # ── TTS ＋ タイムライン構築 ─────────────────────────────────
        backend_label = f"VOICEVOX" if TTS_BACKEND == "voicevox" else "edge-tts"
        update_status(job_id, "processing", 5, f"日本語音声を生成中（{backend_label} / {voice_key}）...")

        with tempfile.TemporaryDirectory() as tmpdir:
            track = AudioSegment.silent(duration=int(total_duration * 1000) + 500)

            for i, seg in enumerate(ja_segments):
                text = seg.get("text", "").strip()
                if not text:
                    continue

                start_ms = int(seg["start"] * 1000)
                end_ms   = int(seg["end"]   * 1000)
                seg_dur  = end_ms - start_ms

                tts_path = os.path.join(tmpdir, f"seg_{i:04d}.mp3")
                try:
                    tts_segment_sync(text, tts_path, voice_key)
                except Exception as e:
                    print(f"[TTS失敗 seg {i}] {e}")
                    continue

                tts_audio = AudioSegment.from_mp3(tts_path)

                # 元の尺に収まらない場合は速度調整
                if len(tts_audio) > seg_dur * 1.05 and seg_dur > 200:
                    tts_audio = adjust_speed(tts_audio, seg_dur)

                track = track.overlay(tts_audio, position=start_ms)

                update_status(
                    job_id, "processing",
                    int(5 + (i + 1) / total * 75),
                    f"音声生成中 ({i + 1}/{total}): {text[:20]}...",
                )

            # ── 音声ファイル書き出し ─────────────────────────────────
            update_status(job_id, "processing", 82, "日本語音声トラックを書き出し中...")
            ja_wav = os.path.join(tmpdir, "japanese_track.wav")
            track.export(ja_wav, format="wav")

            # jobs/<id>/japanese_audio.wav として保存
            import shutil
            shutil.copy(ja_wav, str(job_dir / "japanese_audio.wav"))

            if is_audio_only:
                # 音声ファイル入力 → そのまま出力
                shutil.copy(ja_wav, str(job_dir / "output.mp4"))
                update_status(job_id, "done", 100, "完成しました！（音声ファイル出力）")
                return

            # ── 動画合成 ─────────────────────────────────────────────
            update_status(job_id, "processing", 88, "動画に日本語音声を合成中...")
            output_path = str(job_dir / "output.mp4")

            result = subprocess.run(
                ["ffmpeg", "-y",
                 "-i", input_path,
                 "-i", ja_wav,
                 "-c:v", "copy",
                 "-c:a", "aac", "-b:a", "192k",
                 "-map", "0:v:0",
                 "-map", "1:a:0",
                 "-shortest",
                 output_path],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"動画合成エラー: {result.stderr[-500:]}")

        update_status(job_id, "done", 100, "完成しました！動画をダウンロードできます。")

    except Exception as e:
        update_status(job_id, "error", 0, f"エラー: {e}")
        raise
