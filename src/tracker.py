"""Camera capture (opened by device NAME) + MediaPipe FaceLandmarker head pose.

All processing is local. No frame is ever written to disk or sent anywhere.
"""
import math
import sys
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

IS_WIN = sys.platform == "win32"



# --------------------------------------------------------------------------- camera
class Camera(threading.Thread):
    """Grabs frames in a background thread and keeps only the newest one
    (avoids processing stale buffered frames)."""

    def __init__(self, index=0, width=640, height=480, fps=60, exposure=None):
        super().__init__(daemon=True)
        self.index = index
        backend = cv2.CAP_DSHOW if IS_WIN else cv2.CAP_ANY
        self.cap = cv2.VideoCapture(index, backend)
        if not self.cap.isOpened():
            raise RuntimeError(f"camera {index} cannot be opened")
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # In dim light, auto-exposure lengthens exposure time and the frame rate
        # drops (e.g. 30 -> 10 fps). A fixed short exposure keeps the frame rate.
        # DirectShow exposure is log2 seconds: -5 = 31 ms, -6 = 16 ms, -7 = 8 ms.
        if exposure is not None:
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)   # manual (DSHOW convention)
            self.cap.set(cv2.CAP_PROP_EXPOSURE, float(exposure))
        else:
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)   # auto (also undoes an earlier manual run)
        self.cond = threading.Condition()
        self.frame = None
        self.t = 0.0
        self.seq = 0
        self.running = True
        self.intervals = []  # recent frame intervals (s)
        self.size = (int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                     int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

    def run(self):
        last = None
        fails = 0
        while self.running:
            ok, img = self.cap.read()
            now = time.perf_counter()
            if not ok or img is None:
                fails += 1
                if fails > 100:
                    break
                time.sleep(0.01)
                continue
            fails = 0
            with self.cond:
                self.frame, self.t = img, now
                self.seq += 1
                if last is not None:
                    self.intervals.append(now - last)
                    if len(self.intervals) > 120:
                        del self.intervals[:-120]
                self.cond.notify_all()
            last = now

    def get(self, last_seq, timeout=1.0):
        """Wait for a frame newer than last_seq. Returns (seq, img, t) or None."""
        with self.cond:
            if not self.cond.wait_for(lambda: self.seq > last_seq or not self.running, timeout):
                return None
            if self.frame is None:
                return None
            return self.seq, self.frame, self.t

    def open_settings(self):
        """Open the camera driver's own settings dialog (exposure, low-light
        compensation, ...). DirectShow only."""
        self.cap.set(cv2.CAP_PROP_SETTINGS, 1)

    def fps(self):
        iv = self.intervals[-60:]
        return (1.0 / (sum(iv) / len(iv))) if iv else 0.0

    def stop(self):
        self.running = False
        self.join(timeout=1.0)
        self.cap.release()


def camera_names():
    """DirectShow device names in index order, WITHOUT opening any device.
    Index i matches cv2.VideoCapture(i, cv2.CAP_DSHOW)."""
    if not IS_WIN:
        return []
    try:
        from pygrabber.dshow_graph import FilterGraph
        return list(FilterGraph().get_input_devices())
    except Exception as e:
        print("cannot list camera names:", e)
        return []


def resolve_camera(name):
    """Return the DSHOW index whose device name matches `name` exactly.
    Never guesses another device (some indices are phone/virtual cameras)."""
    names = camera_names()
    for i, n in enumerate(names):
        if n == name:
            return i, names
    return None, names


# --------------------------------------------------------------------------- features
NOSE_TIP = 1
DRAW_PTS = [NOSE_TIP, 33, 263, 61, 291, 10, 152, 234, 454]  # nose, eye corners, mouth, forehead, chin, cheeks


@dataclass
class Features:
    ok: bool = False
    yaw: float = 0.0         # head yaw (left/right turn), degrees
    pitch: float = 0.0       # head pitch, degrees (display only)
    nose_x: float = 0.0      # nose tip x, normalised image coords (sideways shift)
    nose_rel: float = 0.0    # nose tip relative to cheek midpoint / face width (turn, distance-invariant)
    pts: dict = field(default_factory=dict)  # pixel coords for drawing


class FaceTracker:
    def __init__(self, model_path, use_gpu=False):
        from mediapipe.tasks.python import BaseOptions, vision
        # read as bytes: MediaPipe's native loader can choke on non-ASCII paths
        with open(model_path, "rb") as f:
            buf = f.read()
        delegate = BaseOptions.Delegate.GPU if use_gpu else BaseOptions.Delegate.CPU
        opts = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=buf, delegate=delegate),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=True,
        )
        self.lm = vision.FaceLandmarker.create_from_options(opts)
        self._last_ts = -1
        import mediapipe as mp
        self._mp = mp

    def process(self, bgr):
        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        ts = int(time.perf_counter() * 1000)
        if ts <= self._last_ts:
            ts = self._last_ts + 1
        self._last_ts = ts
        res = self.lm.detect_for_video(img, ts)
        f = Features()
        if not res.face_landmarks or not res.facial_transformation_matrixes:
            return f
        lms = res.face_landmarks[0]
        R = np.asarray(res.facial_transformation_matrixes[0])[:3, :3]
        f.yaw = math.degrees(math.atan2(R[0, 2], R[2, 2]))
        f.pitch = math.degrees(math.asin(max(-1.0, min(1.0, -R[1, 2]))))
        f.nose_x = float(lms[NOSE_TIP].x)
        l, r = lms[234].x, lms[454].x       # face edges (cheeks)
        if abs(r - l) > 1e-6:
            f.nose_rel = float((lms[NOSE_TIP].x - (l + r) / 2) / abs(r - l))
        f.pts = {i: (lms[i].x * w, lms[i].y * h) for i in DRAW_PTS if i < len(lms)}
        f.ok = True
        return f

    def close(self):
        self.lm.close()
