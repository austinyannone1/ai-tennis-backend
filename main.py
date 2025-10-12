# main.py
from utils_features import compute_features_from_keypoints
import os
import uuid
import shutil
import threading
from typing import Optional, List, Dict

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Supabase admin client (service role)
from supabase import create_client, Client

# ---------------- App & CORS ----------------
app = FastAPI()

# For MVP it's fine to allow all; later, restrict to your app domains
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- Health ----------------
@app.get("/health")
def health():
    return {"status": "ok"}

# ---------------- Mock analyzers (swap with real model later) ----------------
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

# ---------------- Existing /analyze (direct file upload) ----------------
@app.post("/analyze")
async def analyze_video(
    file: UploadFile = File(...),
    stroke_type: str = Form(...)
):
    """
    Keeps your original behavior for direct file uploads from the app.
    Saves to a temp file, runs the mock analyzer, returns JSON.
    """
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

        # Try to clean temp file; ignore if already gone
        try:
            os.remove(temp_filename)
        except Exception:
            pass

        if "error" in result:
            return JSONResponse(status_code=400, content=result)
        return JSONResponse(content=result)

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ---------------- New analyze-from-storage (async background) ----------------
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
    """
    Runs in a background thread:
    - (Optional) Download the file if your real model needs local path
    - Run analyzer
    - Update public.videos with analysis JSON + status
    """
    if not supabase_admin:
        # We can't update DB without admin client; try to mark failed if possible
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

        # Write result & set status completed
        supabase_admin.table("videos").update({
            "analysis": result,           # change to "analysis_json" if your column is named that
            "analysis_status": "completed",
            "error_message": None,
        }).eq("id", video_id).execute()

    except Exception as e:
        # Mark as failed and store error string
        try:
            supabase_admin.table("videos").update({
                "analysis_status": "failed",
                "error_message": str(e),
            }).eq("id", video_id).execute()
        except Exception:
            pass

@app.post("/analyze-from-storage")
async def analyze_from_storage(payload: AnalyzeFromStoragePayload):
    """
    App flow:
      1) Upload to Supabase Storage (bucket: videos)
      2) Insert row in public.videos with analysis_status='pending'
      3) Call POST /analyze-from-storage with { video_id, storage_path, stroke_type }
    """
    if not supabase_admin:
        return JSONResponse(status_code=500, content={"error": "Supabase admin client not configured"})

    try:
        # Flip to processing now
        supabase_admin.table("videos").update({
            "analysis_status": "processing",
            "error_message": None,
        }).eq("id", payload.video_id).execute()

        # Start background analysis
        t = threading.Thread(
            target=do_analysis_and_update,
            args=(payload.video_id, payload.storage_path, payload.stroke_type),
            daemon=True,
        )
        t.start()

        return {"status": "accepted", "video_id": payload.video_id}
    except Exception as e:
        # If even starting fails, mark failed
        try:
            supabase_admin.table("videos").update({
                "analysis_status": "failed",
                "error_message": str(e),
            }).eq("id", payload.video_id).execute()
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"error": str(e)})

# ---------- Feature computation from keypoints (MVP test endpoint) ----------
class FeatureRequest(BaseModel):
    """
    Request body:
    {
      "fps": 30,
      "stroke_type": "forehand",
      "frames": [ { keypoint_name: { "x": <float>, "y": <float> }, ... }, ... ]
    }
    """
    fps: float = 30.0
    stroke_type: str = "forehand"
    frames: List[Dict[str, Dict[str, float]]]

@app.post("/features/compute")
def compute_features_endpoint(req: FeatureRequest):
    """
    Accepts a list of frames where each frame is a dict of
    keypoint_name -> {x, y}. Computes the features and returns them.
    """
    try:
        # Convert incoming dicts into the shape utils_features expects:
        # List[ Dict[str, Tuple[float,float]] ]
        frames_xy: List[Dict[str, tuple]] = []
        for f in req.frames:
            frame_xy: Dict[str, tuple] = {}
            for name, kp in f.items():
                x = kp.get("x")
                y = kp.get("y")
                if x is None or y is None:
                    continue
                frame_xy[name] = (float(x), float(y))
            if frame_xy:
                frames_xy.append(frame_xy)

        if not frames_xy:
            return JSONResponse(status_code=400, content={"error": "No valid frames with (x,y) provided"})

        # ✅ Call without stroke_type — your util doesn't accept it
        feats = compute_features_from_keypoints(frames_xy, fps=req.fps)

        return {"ok": True, "fps": req.fps, "stroke_type": req.stroke_type, "features": feats}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

