import os
import uuid
import shutil
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import boto3
from botocore.exceptions import ClientError


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


def get_r2_client():
    """Initialize S3-compatible Cloudflare R2 client."""
    account_id = os.getenv("R2_ACCOUNT_ID")
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    
    if not all([account_id, access_key, secret_key]):
        raise HTTPException(
            status_code=500,
            detail="R2 credentials not configured. Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY."
        )
    
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def upload_to_r2(file_path: Path, bucket_name: str, object_name: str) -> str:
    """
    Upload file to Cloudflare R2 and return the public URL.
    
    Args:
        file_path: Path to the file to upload
        bucket_name: R2 bucket name
        object_name: Object key/name in R2
    
    Returns:
        Public URL of the uploaded file
    """
    try:
        s3_client = get_r2_client()
        
        with open(file_path, "rb") as f:
            s3_client.upload_fileobj(
                f,
                bucket_name,
                object_name,
                ExtraArgs={"ContentType": "audio/mpeg"}
            )
        
        # Construct public URL (assumes bucket has public access configured)
        account_id = os.getenv("R2_ACCOUNT_ID")
        public_url = f"https://{bucket_name}.{account_id}.r2.cloudflarestorage.com/{object_name}"
        
        return public_url
    
    except ClientError as e:
        raise HTTPException(
            status_code=500,
            detail=f"R2 upload failed: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Upload error: {str(e)}"
        )


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
    
    audio_url = None

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

        # Upload to Cloudflare R2
        bucket_name = os.getenv("R2_BUCKET_NAME")
        if not bucket_name:
            raise HTTPException(
                status_code=500,
                detail="R2_BUCKET_NAME not configured."
            )
        
        # Generate unique filename: {job_id}.mp3
        r2_object_name = f"{job_id}.mp3"
        audio_url = upload_to_r2(mp3_path, bucket_name, r2_object_name)

        return {
            "title": info.get("title") or "TikTok Sound",
            "creator": info.get("uploader") or info.get("channel") or "Unknown creator",
            "duration": int(info.get("duration") or 0),
            "audio_url": audio_url,
            "thumbnail_url": info.get("thumbnail"),
            "source_url": payload.url,
            "tags": ["extracted", "tiktok", "looped"],
            "debug": {
                "mp3_created": mp3_path.exists(),
                "mp3_size_bytes": mp3_path.stat().st_size if mp3_path.exists() else 0,
                "r2_uploaded": audio_url is not None,
                "r2_object_name": r2_object_name
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")

    finally:
        # Clean up temporary files
        shutil.rmtree(temp_dir, ignore_errors=True)
