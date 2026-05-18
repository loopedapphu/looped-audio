import os
import uuid
import shutil
import subprocess
from pathlib import Path

import boto3
import yt_dlp
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


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


def get_env(name: str) -> str:
    return os.getenv(name, "").strip()


def get_r2_client():
    account_id = get_env("R2_ACCOUNT_ID")
    access_key = get_env("R2_ACCESS_KEY_ID")
    secret_key = get_env("R2_SECRET_ACCESS_KEY")
    endpoint_url = get_env("R2_ENDPOINT_URL")

    if not endpoint_url and account_id:
        endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

    if not account_id:
        raise HTTPException(status_code=500, detail="R2_ACCOUNT_ID missing.")

    if not access_key:
        raise HTTPException(status_code=500, detail="R2_ACCESS_KEY_ID missing.")

    if not secret_key:
        raise HTTPException(status_code=500, detail="R2_SECRET_ACCESS_KEY missing.")

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


def upload_to_r2(file_path: Path, object_key: str) -> str:
    bucket_name = get_env("R2_BUCKET_NAME")
    public_url = get_env("R2_PUBLIC_URL")

    if not bucket_name:
        raise HTTPException(status_code=500, detail="R2_BUCKET_NAME missing.")

    if not public_url:
        raise HTTPException(status_code=500, detail="R2_PUBLIC_URL missing.")

    try:
        client = get_r2_client()

        client.upload_file(
            Filename=str(file_path),
            Bucket=bucket_name,
            Key=object_key,
            ExtraArgs={
                "ContentType": "audio/mpeg",
            },
        )

        return f"{public_url.rstrip('/')}/{object_key}"

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        raise HTTPException(
            status_code=500,
            detail=f"R2 upload failed: {error_code} - {error_message}",
        )


@app.get("/")
def root():
    return {"status": "LOOPED backend is alive"}


@app.get("/debug/r2")
def debug_r2():
    account_id = get_env("R2_ACCOUNT_ID")
    access_key = get_env("R2_ACCESS_KEY_ID")
    secret_key = get_env("R2_SECRET_ACCESS_KEY")
    bucket_name = get_env("R2_BUCKET_NAME")
    public_url = get_env("R2_PUBLIC_URL")
    endpoint_url = get_env("R2_ENDPOINT_URL") or (
        f"https://{account_id}.r2.cloudflarestorage.com" if account_id else ""
    )

    return {
        "R2_ACCOUNT_ID_set": bool(account_id),
        "R2_ACCOUNT_ID_preview": f"{account_id[:6]}...{account_id[-6:]}" if len(account_id) > 12 else account_id,
        "R2_ENDPOINT_URL": endpoint_url,
        "R2_ACCESS_KEY_ID_set": bool(access_key),
        "R2_ACCESS_KEY_ID_preview": f"{access_key[:4]}...{access_key[-4:]}" if len(access_key) > 8 else access_key,
        "R2_SECRET_ACCESS_KEY_set": bool(secret_key),
        "R2_SECRET_ACCESS_KEY_length": len(secret_key),
        "R2_BUCKET_NAME": bucket_name,
        "R2_PUBLIC_URL": public_url,
    }


@app.get("/debug/r2-list")
def debug_r2_list():
    bucket_name = get_env("R2_BUCKET_NAME")

    if not bucket_name:
        raise HTTPException(status_code=500, detail="R2_BUCKET_NAME missing.")

    try:
        client = get_r2_client()
        response = client.list_objects_v2(
            Bucket=bucket_name,
            MaxKeys=5,
        )

        return {
            "ok": True,
            "bucket": bucket_name,
            "contents_count": len(response.get("Contents", [])),
            "keys": [item.get("Key") for item in response.get("Contents", [])],
        }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        raise HTTPException(
            status_code=500,
            detail=f"R2 list failed: {error_code} - {error_message}",
        )


@app.post("/extract")
def extract(payload: ExtractRequest):
    if not payload.url or "tiktok.com" not in payload.url:
        raise HTTPException(status_code=400, detail="Please provide a valid TikTok URL.")

    job_id = str(uuid.uuid4())
    temp_dir = Path("/tmp") / f"looped_{job_id}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    raw_audio_path = temp_dir / "source.%(ext)s"
    mp3_path = temp_dir / "sound.mp3"
    object_key = f"sounds/{job_id}.mp3"

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

        audio_url = upload_to_r2(mp3_path, object_key)

        return {
            "title": info.get("title") or "TikTok Sound",
            "creator": info.get("uploader") or info.get("channel") or "Unknown creator",
            "duration": int(info.get("duration") or 0),
            "audio_url": audio_url,
            "audio_key": object_key,
            "thumbnail_url": info.get("thumbnail"),
            "source_url": payload.url,
            "tags": ["extracted", "tiktok", "looped"],
            "debug": {
                "mp3_created": mp3_path.exists(),
                "mp3_size_bytes": mp3_path.stat().st_size if mp3_path.exists() else 0,
                "r2_uploaded": True,
                "r2_object_key": object_key,
            },
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)