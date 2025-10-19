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

# --- import your util + (try) the joint index map ---
from utils_features import compute_features_from_keypoints
try:
    # if utils_features exposes MP, use it
    from utils_features import MP as MP_UTIL
except Exception:
    MP_UTIL = None

# Fallback joint index map (must match utils_features.py)
MP_FALLBACK: Dict[str, int] = {
    "left_shoulder": 11, "right_shoulder": 12,
    "left_elbow": 13,    "right_elbow": 14,
    "left_wrist": 15,    "right_wrist": 16,
    "left_hip": 23,      "right_hip": 24,
}
MP: Dict[str, int] = MP_UTIL or MP_FALLBACK

# -----------------------------------------------------------------------------
# App & CORS
# -----------------------------------------------------------------------------
app = FastAPI()
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
# Mock analyzers (placeholder)
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
# /analyze (direct upload)
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
# /analyze-from-storage (async save to Supabase)
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
    storage_path: str
    stroke_type: str

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
            "analysis": result,           # use "analysis_json" if that's your column
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
# /features/compute (convert name→index to satisfy utils)
# -----------------------------------------------------------------------------
class Keypoint(BaseModel):
    x: float
    y: float
    confidence: Optional[float] = Field(default=None)

class PhaseMark(BaseModel):
    frame: int
    phase: str

class FeatureRequest(BaseModel):
    fps: float = 30
    stroke_type: Optional[str] = "forehand"
    frames: List[Dict[str, Keypoint]]        # name-keyed points
    phases: Optional[List[PhaseMark]] = None # optional

@app.post("/features/compute")
def compute_features_endpoint(req: FeatureRequest):
    try:
        if not req.frames:
            return JSONResponse(status_code=400, content={"error": "frames must be a non-empty list"})
        if req.fps <= 0:
            return JSONResponse(status_code=400, content={"error": "fps must be > 0"})

        # First: name→(x,y)
        frames_named: List[Dict[str, tuple]] = []
        for f in req.frames:
            d: Dict[str, tuple] = {}
            for name, kp in f.items():
                if kp is None:
                    continue
                d[name] = (float(kp.x), float(kp.y))
            if d:
                frames_named.append(d)

        if not frames_named:
            return JSONResponse(status_code=400, content={"error": "No valid frames with (x,y) provided"})

        # Then convert name→index to satisfy utils_features (avoids KeyError: 11)
        frames_indexed: List[Dict[int, tuple]] = []
        for d in frames_named:
            di: Dict[int, tuple] = {}
            for name, xy in d.items():
                idx = MP.get(name)
                if idx is None:
                    # silently skip unknown keypoints
                    continue
                di[idx] = xy
            if di:
                frames_indexed.append(di)

        if not frames_indexed:
            return JSONResponse(
                status_code=400,
                content={"error": "After mapping names to indices, no usable keypoints remained. Check your keypoint names."}
            )

        # Phases → list of dicts
        phases_list: List[Dict[str, object]] = []
        if req.phases:
            for p in req.phases:
                if isinstance(p.frame, int) and p.phase:
                    phases_list.append({"frame": int(p.frame), "phase": p.phase})

        # Call util: (frames_xy, phases, fps)
        features = compute_features_from_keypoints(frames_indexed, phases_list, float(req.fps))

        return {
            "ok": True,
            "fps": float(req.fps),
            "stroke_type": req.stroke_type,
            "features": features,
        }

    except Exception as e:
        payload = {"error": f"{type(e).__name__}: {str(e)}"}
        if os.environ.get("DEBUG", "").lower() in ("1", "true", "yes"):
            payload["trace"] = traceback.format_exc()
        return JSONResponse(status_code=500, content=payload)
