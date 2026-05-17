import os
import uuid
import shutil
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp


app = FastAPI(title="LOOPED Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExtractRequest(BaseModel):
    url: str


@app.get("/")
def root():
    return {"status": "LOOPED backend is alive"}


@app.post("/extract")
def extract(payload: ExtractRequest):
    if not payload.url or "tiktok.com" not in payload.url:
        raise HTTPException(status_code=400, detail="Please provide a valid TikTok URL.")

    job_id = str(uuid.uuid4())
    temp_dir = Path("/tmp") / f"looped_{job_id}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    raw_audio_path = temp_dir / "source.%(ext)s"
    mp3_path = temp_dir / "sound.mp3"

    try:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(raw_audio_path),
            "quiet": True,
            "noplaylist": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(payload.url, download=True)

        downloaded_files = list(temp_dir.glob("source.*"))
        if not downloaded_files:
            raise HTTPException(status_code=500, detail="Audio download failed.")

        source_file = downloaded_files[0]

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source_file),
                "-vn",
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "2",
                str(mp3_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return {
            "title": info.get("title") or "TikTok Sound",
            "creator": info.get("uploader") or info.get("channel") or "Unknown creator",
            "duration": int(info.get("duration") or 0),
            "audio_url": None,
            "thumbnail_url": info.get("thumbnail"),
            "source_url": payload.url,
            "tags": ["extracted", "tiktok", "looped"],
            "debug": {
                "mp3_created": mp3_path.exists(),
                "mp3_size_bytes": mp3_path.stat().st_size if mp3_path.exists() else 0
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)