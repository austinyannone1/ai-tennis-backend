from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
import shutil
import uuid
import os

app = FastAPI()

def analyze_forehand(video_path: str) -> dict:
    # This is dummy output for now — we'll replace with real model later
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

@app.post("/analyze")
async def analyze_video(file: UploadFile = File(...)):
    try:
        temp_filename = f"upload_{uuid.uuid4()}.mp4"
        with open(temp_filename, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        result = analyze_forehand(temp_filename)
        os.remove(temp_filename)

        return JSONResponse(content=result)

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
