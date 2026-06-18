"""
FastAPI バックエンド — 英語→日本語 自動吹き替えシステム
"""

import json
import shutil
import uuid
from pathlib import Path
from typing import List

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.pipeline import run_transcription, run_pipeline, update_status

app = FastAPI(title="英語→日本語 自動吹き替えシステム")

JOBS_DIR = Path("jobs")
JOBS_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


# ── ページ配信 ────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse("app/static/index.html")


@app.get("/edit")
async def edit_page():
    return FileResponse("app/static/edit.html")


# ── ファイルアップロード ──────────────────────────────────────────────
@app.post("/upload")
async def upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    model: str = Form(default="medium"),
):
    job_id  = str(uuid.uuid4())[:8]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir()

    suffix     = Path(file.filename).suffix or ".mp4"
    input_path = job_dir / f"original{suffix}"

    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    update_status(job_id, "uploaded", 0, "アップロード完了。文字起こしを開始します...")
    background_tasks.add_task(run_transcription, job_id, str(input_path), model)

    return {"job_id": job_id}


# ── URL からダウンロード（オプション） ───────────────────────────────
@app.post("/download_url")
async def download_from_url(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    model: str = Form(default="medium"),
):
    import subprocess
    job_id  = str(uuid.uuid4())[:8]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir()

    update_status(job_id, "downloading", 0, "URLから動画をダウンロード中...")

    def _download_and_transcribe():
        try:
            output_path = str(job_dir / "original.%(ext)s")
            result = subprocess.run(
                ["yt-dlp", "-o", output_path, "--no-playlist", url],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                update_status(job_id, "error", 0, f"ダウンロード失敗: {result.stderr[-300:]}")
                return

            video_files = list(job_dir.glob("original.*"))
            if not video_files:
                update_status(job_id, "error", 0, "ダウンロードされたファイルが見つかりません")
                return

            update_status(job_id, "downloaded", 5, "ダウンロード完了。文字起こしを開始します...")
            run_transcription(job_id, str(video_files[0]), model)
        except Exception as e:
            update_status(job_id, "error", 0, f"ダウンロードエラー: {e}")

    background_tasks.add_task(_download_and_transcribe)
    return {"job_id": job_id}


# ── ジョブ状態確認 ────────────────────────────────────────────────────
@app.get("/jobs/{job_id}/status")
async def get_status(job_id: str):
    path = JOBS_DIR / job_id / "status.json"
    if not path.exists():
        return JSONResponse({"error": "ジョブが見つかりません"}, status_code=404)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── セグメント取得（編集済み優先） ────────────────────────────────────
@app.get("/jobs/{job_id}/segments")
async def get_segments(job_id: str):
    job_dir = JOBS_DIR / job_id
    for name in ["segments_ja_edited.json", "segments_ja.json"]:
        path = job_dir / name
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    return JSONResponse({"error": "セグメントが見つかりません"}, status_code=404)


# ── 英語セグメント取得（編集画面の参照用） ─────────────────────────────
@app.get("/jobs/{job_id}/segments_en")
async def get_segments_en(job_id: str):
    path = JOBS_DIR / job_id / "segments_en.json"
    if not path.exists():
        return JSONResponse({"error": "英語セグメントが見つかりません"}, status_code=404)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── セグメント保存 ────────────────────────────────────────────────────
class Segment(BaseModel):
    start: float
    end:   float
    text:  str


@app.put("/jobs/{job_id}/segments")
async def save_segments(job_id: str, segments: List[Segment] = Body(...)):
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        return JSONResponse({"error": "ジョブが見つかりません"}, status_code=404)

    path = job_dir / "segments_ja_edited.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump([s.model_dump() for s in segments], f, ensure_ascii=False, indent=2)

    return {"saved": True, "count": len(segments)}


# ── 処理実行 ──────────────────────────────────────────────────────────
@app.post("/jobs/{job_id}/run")
async def run_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    voice: str = Form(default="female"),
    subtitle: bool = Form(default=True),
):
    if not (JOBS_DIR / job_id).exists():
        return JSONResponse({"error": "ジョブが見つかりません"}, status_code=404)

    update_status(job_id, "processing", 0, "処理を開始しています...")
    background_tasks.add_task(run_pipeline, job_id, voice, subtitle)

    return {"started": True}


# ── 完成動画ダウンロード ──────────────────────────────────────────────
@app.get("/jobs/{job_id}/download")
async def download_video(job_id: str):
    output_path = JOBS_DIR / job_id / "output.mp4"
    if not output_path.exists():
        return JSONResponse({"error": "出力動画がまだ完成していません"}, status_code=404)

    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename="output_japanese.mp4",
    )


# ── 字幕ファイルダウンロード ──────────────────────────────────────────
@app.get("/jobs/{job_id}/subtitle")
async def download_subtitle(job_id: str):
    sub_path = JOBS_DIR / job_id / "subtitle.srt"
    if not sub_path.exists():
        return JSONResponse({"error": "字幕ファイルがまだ完成していません"}, status_code=404)

    return FileResponse(
        sub_path,
        media_type="text/plain; charset=utf-8",
        filename="subtitle_japanese.srt",
    )


# ── 音声ファイルダウンロード ──────────────────────────────────────────
@app.get("/jobs/{job_id}/audio")
async def download_audio(job_id: str):
    audio_path = JOBS_DIR / job_id / "japanese_audio.wav"
    if not audio_path.exists():
        return JSONResponse({"error": "音声ファイルがまだ完成していません"}, status_code=404)

    return FileResponse(
        audio_path,
        media_type="audio/wav",
        filename="japanese_audio.wav",
    )
