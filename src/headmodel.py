"""Head-pose -> horizontal screen fraction (per-user calibration) and One Euro filter."""
import json
import math

import numpy as np

CALIB_VERSION = 3


class OneEuro:
    """One Euro filter: low cutoff when still (less jitter),
    cutoff rises with speed (less lag on fast movements)."""

    def __init__(self, min_cutoff=1.5, beta=1.0, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self):
        self.x = None
        self.dx = 0.0
        self.t = None

    @staticmethod
    def _a(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t):
        if self.x is None:
            self.x, self.t, self.dx = x, t, 0.0
            return x
        dt = max(t - self.t, 1e-4)
        dx = (x - self.x) / dt
        ad = self._a(self.d_cutoff, dt)
        self.dx = ad * dx + (1 - ad) * self.dx
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        a = self._a(cutoff, dt)
        self.x = a * x + (1 - a) * self.x
        self.t = t
        return self.x


class HeadModel:
    """Head-pose control: piecewise-linear map of head yaw (or nose x position)
    from the user's own LEFT / CENTER / RIGHT poses to screen fraction 0 / 0.5 / 1.
    The user chooses the amplitude: a slight turn is enough."""
    SIGNALS = ("yaw", "nose_x", "nose_rel")

    def __init__(self, signal="yaw"):
        self.signal = signal
        self.pts = None   # {signal: {"left": v, "center": v, "right": v}}
        self.info = {}

    @property
    def ready(self):
        return self.pts is not None and self.signal in self.pts

    @staticmethod
    def value(f, signal):
        return {"yaw": f.yaw, "nose_x": f.nose_x, "nose_rel": f.nose_rel}[signal]

    def fit(self, poses):
        """poses: dict pose -> list of (yaw, nose_x, nose_rel) samples; pose in left/center/right."""
        for k in ("left", "center", "right"):
            if len(poses.get(k, [])) < 5:
                raise ValueError(f"not enough samples for {k.upper()} pose")
        pts, spans = {}, {}
        for j, s in enumerate(self.SIGNALS):
            m = {k: float(np.median([v[j] for v in poses[k]])) for k in ("left", "center", "right")}
            pts[s] = m
            spans[s] = m["right"] - m["left"]
        y = pts["yaw"]
        if not ((y["left"] < y["center"] < y["right"]) or (y["left"] > y["center"] > y["right"])):
            raise ValueError("LEFT/CENTER/RIGHT yaw not in order - turn a bit more, or check the camera")
        if abs(spans["yaw"]) < 3.0:
            raise ValueError(f"head turn too small ({abs(spans['yaw']):.1f} deg) - turn a bit more")
        self.pts = pts
        # per-pose jitter, expressed as screen fraction (how steady a held pose maps)
        noise = {}
        for j, s in enumerate(self.SIGNALS):
            half = abs(pts[s]["right"] - pts[s]["left"]) / 2 or 1e-9
            noise[s] = float(np.median([np.std([v[j] for v in poses[k]]) for k in poses]) / half * 0.5)
        self.info = {"mode": "head", "span_yaw_deg": round(spans["yaw"], 2),
                     "span_nose_x": round(spans["nose_x"], 4), "span_nose_rel": round(spans["nose_rel"], 4),
                     "hold_jitter_frac": {k: round(v, 4) for k, v in noise.items()},
                     "points": pts}
        return self.info

    def predict(self, f):
        p = self.pts[self.signal]
        v = self.value(f, self.signal)
        l, c, r = p["left"], p["center"], p["right"]
        if (v - c) * (r - c) >= 0:
            return 0.5 + 0.5 * (v - c) / (r - c)
        return 0.5 * (v - l) / (c - l)

    def recenter(self, v):
        """Shift all three poses so that value v becomes the centre (drift fix)."""
        p = self.pts[self.signal]
        d = v - p["center"]
        for k in p:
            p[k] += d

    def save(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"version": CALIB_VERSION, "kind": "head", "pts": self.pts, "info": self.info}, fh, indent=2)

    def load(self, path):
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if d.get("kind") != "head":
            raise ValueError("not a head calibration")
        self.pts, self.info = d["pts"], d.get("info", {})
