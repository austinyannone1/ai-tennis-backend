from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
import shutil
import uuid
import os

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

def analyze_forehand(video_path: str) -> dict:
    return {
        "phases": [
            {"frame": 120, "phase": "unit_turn"},
            {"frame": 135, "phase": "racquet_prep"},
            {"frame": 150, "phase": "racquet_takeback"},
            {"frame": 165, "phase": "lag_phase"},
            {"frame": 180, "phase": "contact"},
            {"frame": 195, "phase": "extension"},
            {"frame": 210, "phase": "follow_through"},
            {"frame": 225, "phase": "unknown"}
        ],
        "feedback": [
            {"phase": "contact", "tip": "Try making contact further in front of the body"},
            {"phase": "lag_phase", "tip": "Keep wrist more relaxed to generate racquet lag"},
            {"phase": "follow_through", "tip": "Finish higher for more topspin"}
        ]
    }

def analyze_backhand(video_path: str) -> dict:
    return {
        "phases": [
            {"frame": 110, "phase": "unit_turn"},
            {"frame": 130, "phase": "racquet_prep"},
            {"frame": 150, "phase": "contact"},
            {"frame": 170, "phase": "follow_through"}
        ],
        "feedback": [
            {"phase": "contact", "tip": "Step into the ball more"},
            {"phase": "follow_through", "tip": "Extend further across for more control"}
        ]
    }

def analyze_serve(video_path: str) -> dict:
    return {
        "phases": [
            {"frame": 100, "phase": "trophy_position"},
            {"frame": 120, "phase": "racquet_drop"},
            {"frame": 140, "phase": "contact"},
            {"frame": 160, "phase": "follow_through"}
        ],
        "feedback": [
            {"phase": "trophy_position", "tip": "Keep tossing arm straighter"},
            {"phase": "contact", "tip": "Hit more on top for spin"}
        ]
    }

@app.post("/analyze")
async def analyze_video(
    file: UploadFile = File(...),
    stroke_type: str = Form(...)
):
    try:
        temp_filename = f"upload_{uuid.uuid4()}.mp4"
        with open(temp_filename, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        if stroke_type.lower() == "forehand":
            result = analyze_forehand(temp_filename)
        elif stroke_type.lower() == "backhand":
            result = analyze_backhand(temp_filename)
        elif stroke_type.lower() == "serve":
            result = analyze_serve(temp_filename)
        else:
            result = {"error": f"Unsupported stroke type: {stroke_type}"}

        os.remove(temp_filename)
        return JSONResponse(content=result)

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# CORS — add your Bolt preview origin if you have it; "*" is okay for MVP
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or ["https://your-bolt-preview-domain"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
