# utils_features.py  (FINAL VERSION)
import math
from typing import Dict, List, Tuple, Optional

MP = {
    "left_shoulder": 11, "right_shoulder": 12,
    "left_elbow": 13, "right_elbow": 14,
    "left_wrist": 15, "right_wrist": 16,
    "left_hip": 23, "right_hip": 24,
}

Point = Tuple[float, float]

def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0]-b[0], a[1]-b[1])

def _angle_at(q: Point, p: Point, r: Point) -> Optional[float]:
    """Angle p-q-r at q (degrees)."""
    v1 = (p[0]-q[0], p[1]-q[1])
    v2 = (r[0]-q[0], r[1]-q[1])
    n1 = math.hypot(*v1); n2 = math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return None
    c = max(-1.0, min(1.0, (v1[0]*v2[0] + v1[1]*v2[1])/(n1*n2)))
    return math.degrees(math.acos(c))

def _shoulder_line_angle_deg(left_sh: Point, right_sh: Point) -> float:
    dx = right_sh[0] - left_sh[0]
    dy = right_sh[1] - left_sh[1]
    if dx == 0 and dy == 0:
        return 0.0
    return math.degrees(math.atan2(dy, dx))

def _normalize(value: float, seg_len: float, eps: float = 1e-6) -> float:
    return value / (seg_len if seg_len > eps else 1.0)

def moving_average(series: List[Point], k: int = 3) -> List[Point]:
    if k <= 1:
        return series
    n = len(series)
    out: List[Point] = []
    half = k // 2
    for i in range(n):
        x_sum = 0.0; y_sum = 0.0; c = 0
        for j in range(max(0, i-half), min(n, i+half+1)):
            x_sum += series[j][0]; y_sum += series[j][1]; c += 1
        out.append((x_sum/c, y_sum/c))
    return out

def compute_features_from_keypoints(
    kps_xy: List[List[Point]],     # [T][33][2]
    phases: List[Dict],            # [{"frame": 180, "phase": "contact"}, ...]
    fps: int = 30,
    right_handed: bool = True
) -> Dict:
    T = len(kps_xy)
    if T == 0:
        return {"meta": {"fps": fps}, "contact": {}, "follow_through": {}, "timing": {}}

    # Smooth tracks we need
    def track(idx: int) -> List[Point]:
        return moving_average([kps_xy[t][idx] for t in range(T)], k=5)

    LSH = track(MP["left_shoulder"]);  RSH = track(MP["right_shoulder"])
    LEL = track(MP["left_elbow"]);     REL = track(MP["right_elbow"])
    LWR = track(MP["left_wrist"]);     RWR = track(MP["right_wrist"])
    LHP = track(MP["left_hip"]);       RHP = track(MP["right_hip"])

    # Phase frames
    def frame_of(name: str, default: int) -> int:
        for p in phases or []:
            if (p.get("phase") or "").lower() == name.lower():
                return int(p.get("frame", default))
        return default

    f_contact = max(0, min(T-1, frame_of("contact", T//2)))
    f_follow  = max(0, min(T-1, frame_of("follow_through", min(T-1, f_contact+12))))
    f_unit    = max(0, min(T-1, frame_of("unit_turn", max(0, f_contact-24))))

    # Choose side (MVP assumes right-handed forehand)
    WR_c = RWR[f_contact] if right_handed else LWR[f_contact]
    EL_c = REL[f_contact] if right_handed else LEL[f_contact]
    SH_c = RSH[f_contact] if right_handed else LSH[f_contact]
    LEAD_HIP_c = LHP[f_contact] if right_handed else RHP[f_contact]

    # Segment lengths at contact
    forearm_len = _dist(EL_c, WR_c)
    upperarm_len = _dist(SH_c, EL_c)

    # 1) Contact: forward offset (wrist ahead of lead hip on x-axis)
    forward_px = (WR_c[0] - LEAD_HIP_c[0])
    contact_forward_norm = _normalize(abs(forward_px), forearm_len)

    # 2) Torso rotation proxy: shoulder line angle magnitude
    shoulder_angle = _shoulder_line_angle_deg(LSH[f_contact], RSH[f_contact])
    torso_rotation_deg = abs(shoulder_angle)

    # 3) Elbow flexion at contact: angle(shoulder, elbow, wrist) at elbow
    # angle p-q-r at q  =>  q = elbow, p = shoulder, r = wrist
    elbow_flex_deg = _angle_at(EL_c, SH_c, WR_c)

    # 4) Follow-through hand height relative to ipsilateral shoulder (normalized)
    WR_f = RWR[f_follow] if right_handed else LWR[f_follow]
    SH_f = RSH[f_follow] if right_handed else LSH[f_follow]
    # y increases downward; height upward => -(dy)
    hand_rel_sh = -(WR_f[1] - SH_f[1])
    follow_through_height_norm = _normalize(hand_rel_sh, upperarm_len)

    # 5) Timing: unit_turn -> contact (frames)
    timing_frames = max(0, f_contact - f_unit)

    return {
        "contact": {
            "forward_offset_norm": contact_forward_norm,
            "torso_rotation_deg": torso_rotation_deg,
            "elbow_flex_deg": elbow_flex_deg,
        },
        "follow_through": {
            "hand_height_norm": follow_through_height_norm,
        },
        "timing": {
            "unit_turn_to_contact_frames": timing_frames,
        },
        "meta": {
            "fps": fps,
            "right_handed": right_handed,
            "norm_segments_px": {
                "forearm": forearm_len,
                "upperarm": upperarm_len,
            }
        }
    }
