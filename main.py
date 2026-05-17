from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="LOOPED Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"status": "LOOPED backend is alive"}

@app.post("/extract")
def extract(payload: dict):
    url = payload.get("url")

    return {
        "title": "Mock TikTok Sound",
        "creator": "LOOPED",
        "duration": 23,
        "audio_url": "https://example.com/mock.mp3",
        "thumbnail_url": None,
        "source_url": url,
        "tags": ["nightdrive", "looped", "test"]
    }