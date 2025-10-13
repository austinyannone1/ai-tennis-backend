# main.py
import os
import uuid
import shutil
import threading
import traceback
from typing import Dict, List, Optional

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from supabase import create_client, Client

# Your feature helper
from utils_features import compute_features_from_keypoints

# -----------------------------------------------------------------------------
# App & CORS
# -----------------------------------------------------------------------------
app = FastAPI()

# MVP: open CORS (tighten later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# Health
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

# -----------------------------------------------------------------------------
# Mock analyzers (placeholder — swap with real model)
# -----------------------------------------------------------------------------
def analyze_forehand(video_path_or_ref: str) -> dict:
    return {
        "phases": [
            {"frame": 120, "phase": "unit_turn"},
            {"frame": 135, "phase": "racquet_prep"},
            {"frame": 150, "phase": "racquet_takeback"},
            {"frame": 165, "phase": "lag_phase"},
            {"frame": 180, "phase": "contact"},
            {"frame": 195, "phase": "extension"},
            {"frame": 210, "phase": "follow_through"},
            {"frame": 225, "phase": "unknown"},
        ],
        "feedback": [
            {"phase": "contact", "tip": "Try making contact further in front of the body"},
            {"phase": "lag_phase", "tip": "Keep wrist more relaxed to generate racquet lag"},
            {"phase": "follow_through", "tip": "Finish higher for more topspin"},
        ],
    }

def analyze_backhand(video_path_or_ref: str) -> dict:
    return {
        "phases": [
            {"frame": 110, "phase": "unit_turn"},
            {"frame": 130, "phase": "racquet_prep"},
            {"frame": 150, "phase": "contact"},
            {"frame": 170, "phase": "follow_through"},
        ],
        "feedback": [
            {"phase": "contact", "tip": "Step into the ball more"},
            {"phase": "follow_through", "tip": "Extend further across for more control"},
        ],
    }

def analyze_serve(video_path_or_ref: str) -> dict:
    return {
        "phases": [
            {"frame": 100, "phase": "trophy_position"},
            {"frame": 120, "phase": "racquet_drop"},
            {"frame": 140, "phase": "contact"},
            {"frame": 160, "phase": "follow_through"},
        ],
        "feedback": [
            {"phase": "trophy_position", "tip": "Keep tossing arm straighter"},
            {"phase": "contact", "tip": "Hit more on top for spin"},
        ],
    }

# -----------------------------------------------------------------------------
# Existing /analyze (direct file upload — returns phases/feedback)
# -----------------------------------------------------------------------------
@app.post("/analyze")
async def analyze_video(
    file: UploadFile = File(...),
    stroke_type: str = Form(...)
):
    try:
        temp_filename = f"upload_{uuid.uuid4()}.mp4"
        with open(temp_filename, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        st = (stroke_type or "").lower()
        if st == "forehand":
            result = analyze_forehand(temp_filename)
        elif st == "backhand":
            result = analyze_backhand(temp_filename)
        elif st == "serve":
            result = analyze_serve(temp_filename)
        else:
            result = {"error": f"Unsupported stroke type: {stroke_type}"}

        try:
            os.remove(temp_filename)
        except Exception:
            pass

        if "error" in result:
            return JSONResponse(status_code=400, content=result)
        return JSONResponse(content=result)

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {str(e)}"})

# -----------------------------------------------------------------------------
# New analyze-from-storage (async → updates Supabase row)
# -----------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

supabase_admin: Optional[Client] = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    try:
        supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception:
        supabase_admin = None

class AnalyzeFromStoragePayload(BaseModel):
    video_id: str
    storage_path: str   # e.g. "<user-id>/<filename>.mp4"
    stroke_type: str    # "forehand" | "backhand" | "serve"

def do_analysis_and_update(video_id: str, storage_path: str, stroke_type: str):
    if not supabase_admin:
        return

    try:
        st = (stroke_type or "").lower()
        if st == "forehand":
            result = analyze_forehand(storage_path)
        elif st == "backhand":
            result = analyze_backhand(storage_path)
        elif st == "serve":
            result = analyze_serve(storage_path)
        else:
            raise ValueError(f"Unsupported stroke type: {stroke_type}")

        supabase_admin.table("videos").update({
            "analysis": result,           # use "analysis_json" instead if that's your column
            "analysis_status": "completed",
            "error_message": None,
        }).eq("id", video_id).execute()

    except Exception as e:
        try:
            supabase_admin.table("videos").update({
                "analysis_status": "failed",
                "error_message": f"{type(e).__name__}: {str(e)}",
            }).eq("id", video_id).execute()
        except Exception:
            pass

@app.post("/analyze-from-storage")
async def analyze_from_storage(payload: AnalyzeFromStoragePayload):
    if not supabase_admin:
        return JSONResponse(status_code=500, content={"error": "Supabase admin client not configured"})

    try:
        supabase_admin.table("videos").update({
            "analysis_status": "processing",
            "error_message": None,
        }).eq("id", payload.video_id).execute()

        t = threading.Thread(
            target=do_analysis_and_update,
            args=(payload.video_id, payload.storage_path, payload.stroke_type),
            daemon=True,
        )
        t.start()

        return {"status": "accepted", "video_id": payload.video_id}
    except Exception as e:
        try:
            supabase_admin.table("videos").update({
                "analysis_status": "failed",
                "error_message": f"{type(e).__name__}: {str(e)}",
            }).eq("id", payload.video_id).execute()
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {str(e)}"})

# -----------------------------------------------------------------------------
# Feature computation from keypoints (high-value metrics)
# -----------------------------------------------------------------------------
class Keypoint(BaseModel):
    x: float
    y: float
    confidence: Optional[float] = Field(default=None)

# Each element: {"frame": 180, "phase": "contact"}
class PhaseMark(BaseModel):
    frame: int
    phase: str

class FeatureRequest(BaseModel):
    fps: float = 30
    stroke_type: Optional[str] = "forehand"   # forwarded in response; util does not need it
    frames: List[Dict[str, Keypoint]]
    phases: Optional[List[PhaseMark]] = None  # optional; pass [] if missing

@app.post("/features/compute")
def compute_features_endpoint(req: FeatureRequest):
    """
    Accepts frames as:
      {
        "fps": 30,
        "stroke_type": "forehand",
        "phases": [{"frame": 180, "phase": "contact"}],
        "frames": [
          {"left_shoulder": {"x":..,"y":..}, "right_hip": {...}, ...},
          ...
        ]
      }
    Returns metrics from utils_features.compute_features_from_keypoints().
    """
    try:
        if not isinstance(req.fps, (int, float)) or req.fps <= 0:
            return JSONResponse(status_code=400, content={"error": "fps must be a positive number"})
        if not req.frames or not isinstance(req.frames, list):
            return JSONResponse(status_code=400, content={"error": "frames must be a non-empty list"})

        # Shape → List[ Dict[str, Tuple[x,y]] ]
        frames_xy: List[Dict[str, tuple]] = []
        for f in req.frames:
            frame_xy: Dict[str, tuple] = {}
            for name, kp in f.items():
                if kp is None:
                    continue
                frame_xy[name] = (float(kp.x), float(kp.y))
            if frame_xy:
                frames_xy.append(frame_xy)

        if not frames_xy:
            return JSONResponse(status_code=400, content={"error": "No valid frames with (x,y) provided"})

        phases_list: List[Dict[str, object]] = []
        if req.phases:
            for p in req.phases:
                if isinstance(p.frame, int) and isinstance(p.phase, str) and p.phase:
                    phases_list.append({"frame": int(p.frame), "phase": p.phase})

        # ✅ utils_features signature: (frames_xy, phases, fps)
        features = compute_features_from_keypoints(frames_xy, phases_list, float(req.fps))

        return {
            "ok": True,
            "fps": float(req.fps),
            "stroke_type": req.stroke_type,
            "features": features,
        }

    except Exception as e:
        payload = {"error": f"{type(e).__name__}: {str(e)}"}
        # Set DEBUG=1 in Render env to include trace
        if os.environ.get("DEBUG", "").lower() in ("1", "true", "yes"):
            payload["trace"] = traceback.format_exc()
        return JSONResponse(status_code=500, content=payload)
