"""Full-screen head calibration / validation / follow-test routines.
All targets lie on one horizontal line; only x matters."""
import math
import random

import cv2
import numpy as np


WHITE, GREY, CYAN, RED, GREEN = (255, 255, 255), (110, 110, 110), (255, 220, 0), (60, 60, 255), (80, 220, 80)


def _text(img, s, x, y, scale=0.8, color=WHITE, th=2):
    cv2.putText(img, s, (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, scale, color, th, cv2.LINE_AA)


def _center_text(img, lines, y0):
    for i, s in enumerate(lines):
        (w, _), _ = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        _text(img, s, (img.shape[1] - w) / 2, y0 + i * 42, 0.9)


class _PointRoutine:
    """Shows targets one at a time: settle (shrinking ring), then collect."""
    INTRO = 3.0

    def __init__(self, fracs, y_frac, settle=0.9, collect=1.0, title=""):
        self.fracs, self.y = fracs, y_frac
        self.settle, self.collect = settle, collect
        self.title = title
        self.t0 = None
        self.done = False
        self.result = None
        self.samples = [[] for _ in fracs]

    def _where(self, now):
        t = now - self.t0 - self.INTRO
        if t < 0:
            return -1, 0.0, False
        per = self.settle + self.collect
        i = int(t // per)
        if i >= len(self.fracs):
            return len(self.fracs), 0.0, False
        tin = t - i * per
        return i, tin, tin >= self.settle

    def update(self, now, valid, f, g_raw, g_filt):
        if self.t0 is None:
            self.t0 = now
        i, _, collecting = self._where(now)
        if i >= len(self.fracs):
            self.done = True
            self.finish()
            return
        if collecting and valid:
            self.samples[i].append(self.sample(f, g_raw, g_filt))

    def target(self, now):
        i, _, _ = self._where(now)
        return self.fracs[i] if 0 <= i < len(self.fracs) else None

    def draw(self, img, now, est=None):
        H, W = img.shape[:2]
        i, tin, collecting = self._where(now)
        y = int(self.y * H)
        cv2.line(img, (0, y), (W, y), (40, 40, 40), 1)
        if i < 0:
            _center_text(img, self.intro, H * 0.25)
            return
        if i >= len(self.fracs):
            return
        x = int(self.fracs[i] * W)
        if getattr(self, "show_est", False) and est is not None and not math.isnan(est):
            cv2.circle(img, (int(min(max(est, 0), 1) * W), y + 30), 6, CYAN, -1)
        if collecting:
            cv2.circle(img, (x, y), 9, WHITE, -1)
            cv2.circle(img, (x, y), 2, (0, 0, 0), -1)
        else:
            r = int(9 + 40 * (1 - tin / self.settle))
            cv2.circle(img, (x, y), r, GREY, 2)
            cv2.circle(img, (x, y), 6, WHITE, -1)
        _text(img, f"{i + 1}/{len(self.fracs)}", 20, 40, 0.8, GREY)


class ValidRoutine(_PointRoutine):
    def __init__(self, y_frac):
        fr = [0.1, 0.3, 0.5, 0.7, 0.9]
        random.shuffle(fr)
        super().__init__(fr, y_frac, settle=1.5, title="VALIDATION")
        self.show_est = True
        self.intro = ["VALIDATION",
                      "Turn your head to put the cyan cursor on each white dot and hold.",
                      "ESC = abort."]

    def sample(self, f, g_raw, g_filt):
        return (g_raw, g_filt)

    def finish(self):
        bias, sd_raw, sd_filt = [], [], []
        for t, s in zip(self.fracs, self.samples):
            if len(s) < 5:
                continue
            a = np.array(s)
            bias.append(np.median(a[:, 0]) - t)
            sd_raw.append(a[:, 0].std())
            sd_filt.append(a[:, 1].std())
        if not bias:
            self.result = {"ok": False, "error": "no valid samples"}
            return
        b = np.array(bias)
        self.result = {"ok": True, "n_points": len(b),
                       "mae_frac": float(np.abs(b).mean()),
                       "max_err_frac": float(np.abs(b).max()),
                       "per_point_err_frac": {f"{t:.1f}": round(float(e), 4) for t, e in zip(self.fracs, b)},
                       "jitter_raw_frac": float(np.median(sd_raw)),
                       "jitter_filt_frac": float(np.median(sd_filt))}


class FollowTest:
    """Phase A: sinusoidal sweeps (smooth pursuit) -> RMSE and lag.
    Phase B: sudden jumps (like a fast flick) -> response time to 50%/90%.
    Response times INCLUDE the player's own reaction + head movement + camera +
    processing + filter. They are NOT the system's end-to-end latency."""
    INTRO = 3.0

    def __init__(self, y_frac, show_est=True, n_jumps=16):
        self.y = y_frac
        self.show_est = show_est
        self.sines = [(0.2, 8.0), (0.5, 8.0), (1.0, 6.0)]  # (Hz, seconds)
        self.sine_T = sum(d for _, d in self.sines)
        self.jumps = []
        t, x = 0.0, 0.5
        for _ in range(n_jumps):
            dur = random.uniform(1.0, 1.4)
            nx = x
            while abs(nx - x) < 0.3:
                nx = random.uniform(0.1, 0.9)
            self.jumps.append((t, x, nx, dur))
            t += dur
            x = nx
        self.jump_T = t
        self.t0 = None
        self.done = False
        self.result = None
        self.log = []  # (t, phase, target, g_raw, g_filt, valid)

    def _phase(self, tt):
        if tt < self.INTRO:
            return "introA", None
        tt -= self.INTRO
        if tt < self.sine_T:
            acc = 0.0
            for hz, d in self.sines:
                if tt < acc + d:
                    return f"sine{hz}", 0.5 + 0.4 * math.sin(2 * math.pi * hz * (tt - acc))
                acc += d
        tt -= self.sine_T
        if tt < self.INTRO:
            return "introB", None
        tt -= self.INTRO
        if tt < self.jump_T:
            for j, (ts, a, b, d) in enumerate(self.jumps):
                if tt < ts + d:
                    return f"jump{j}", b
        return "end", None

    def target(self, now):
        return self._phase(now - self.t0)[1] if self.t0 is not None else None

    def update(self, now, valid, f, g_raw, g_filt):
        if self.t0 is None:
            self.t0 = now
        ph, tgt = self._phase(now - self.t0)
        if ph == "end":
            self.done = True
            self.result = self.analyse()
            return
        if tgt is not None:
            self.log.append((now - self.t0, ph, tgt,
                             g_raw if valid else float("nan"),
                             g_filt if valid else float("nan"), int(valid),
                             int(f.ok), round(f.yaw, 2)))

    def draw(self, img, now, est=None):
        H, W = img.shape[:2]
        ph, tgt = self._phase(now - self.t0) if self.t0 else ("introA", None)
        y = int(self.y * H)
        cv2.line(img, (0, y), (W, y), (40, 40, 40), 1)
        if ph == "introA":
            _center_text(img, ["FOLLOW TEST 1/2: smooth sweeps",
                               "Turn your head to keep the cyan cursor on the white dot. ESC = abort."], H * 0.25)
        elif ph == "introB":
            _center_text(img, ["FOLLOW TEST 2/2: sudden jumps",
                               "Move the cursor to the new dot as fast as you can."], H * 0.25)
        if tgt is not None:
            cv2.circle(img, (int(tgt * W), y), 9, WHITE, -1)
        if self.show_est and est is not None and not math.isnan(est):
            cv2.circle(img, (int(min(max(est, 0), 1) * W), y + 30), 6, CYAN, -1)

    def analyse(self):
        if not self.log:
            return {"ok": False, "error": "no samples"}
        res = {"ok": True}
        acc = self.INTRO
        for hz, d in self.sines:
            off, acc = acc, acc + d
            rows = [r for r in self.log if r[1] == f"sine{hz}" and r[5]]
            if len(rows) < 10:
                continue
            t = np.array([r[0] for r in rows]) - off
            est = np.array([r[4] for r in rows])
            rmse0 = float(np.sqrt(np.mean((est - (0.5 + 0.4 * np.sin(2 * np.pi * hz * t))) ** 2)))
            best = (rmse0, 0)
            for lag_ms in range(0, 801, 10):
                tg = 0.5 + 0.4 * np.sin(2 * np.pi * hz * (t - lag_ms / 1000))
                e = float(np.sqrt(np.mean((est - tg) ** 2)))
                if e < best[0]:
                    best = (e, lag_ms)
            res[f"sine_{hz}Hz"] = {"rmse_frac": rmse0, "best_lag_ms": best[1],
                                   "rmse_after_lag_frac": best[0],
                                   "valid_ratio": len(rows) / max(1, len([r for r in self.log if r[1] == f"sine{hz}"]))}
        # jumps
        jstart = self.INTRO + self.sine_T + self.INTRO
        t50s, t90s, settle_err, hits = [], [], [], 0
        for j, (ts, a, b, d) in enumerate(self.jumps):
            rows = [r for r in self.log if r[1] == f"jump{j}" and r[5]]
            if j == 0 or len(rows) < 5:
                continue
            t = np.array([r[0] for r in rows]) - (jstart + ts)
            est = np.array([r[4] for r in rows])
            prog = (est - a) / (b - a)
            t50 = next((ti for ti, p in zip(t, prog) if p >= 0.5), None)
            t90 = next((ti for ti, p in zip(t, prog) if p >= 0.9), None)
            if t50 is not None:
                t50s.append(t50 * 1000)
            if t90 is not None:
                t90s.append(t90 * 1000)
                if t90 < 0.8:
                    hits += 1
            late = est[t > 0.6]
            if len(late):
                settle_err.append(float(np.median(np.abs(late - b))))
        n = len(self.jumps) - 1
        res["jumps"] = {
            "n": n,
            "reached_90pct_within_800ms": hits,
            "t50_ms_median": float(np.median(t50s)) if t50s else None,
            "t90_ms_median": float(np.median(t90s)) if t90s else None,
            "settled_err_frac_median": float(np.median(settle_err)) if settle_err else None,
            "note": "times include the player's reaction and head movement; not system latency",
        }
        return res


class HeadCalibRoutine:
    """CENTER -> LEFT -> RIGHT -> CENTER. The user picks the amplitude."""
    STEPS = [("center", "Face the screen centre naturally and hold", 0.5),
             ("left", "Turn your head LEFT by the amount that should mean LEFT EDGE, hold", 0.0),
             ("right", "Turn your head RIGHT by the amount that should mean RIGHT EDGE, hold", 1.0),
             ("center", "Back to the centre and hold", 0.5)]
    INTRO, SETTLE, COLLECT = 3.0, 1.6, 1.2

    def __init__(self, model):
        self.model = model
        self.t0 = None
        self.done = False
        self.result = None
        self.poses = {"left": [], "center": [], "right": []}

    def _where(self, now):
        t = now - self.t0 - self.INTRO
        if t < 0:
            return -1, 0.0, False
        per = self.SETTLE + self.COLLECT
        i = int(t // per)
        if i >= len(self.STEPS):
            return len(self.STEPS), 0, False
        tin = t - i * per
        return i, tin, tin >= self.SETTLE

    def target(self, now):
        i, _, _ = self._where(now)
        return self.STEPS[i][2] if 0 <= i < len(self.STEPS) else None

    def update(self, now, valid, f, g_raw, g_filt):
        if self.t0 is None:
            self.t0 = now
        i, _, collecting = self._where(now)
        if i >= len(self.STEPS):
            self.done = True
            try:
                self.result = {"ok": True, **self.model.fit(self.poses)}
            except Exception as e:
                self.result = {"ok": False, "error": str(e)}
            return
        if collecting and f.ok:
            self.poses[self.STEPS[i][0]].append((f.yaw, f.nose_x, f.nose_rel))

    def draw(self, img, now, est=None):
        H, W = img.shape[:2]
        i, tin, collecting = self._where(now)
        cx, cy = W // 2, H // 2
        cv2.drawMarker(img, (cx, cy), GREY, cv2.MARKER_CROSS, 30, 2)
        if i < 0:
            _center_text(img, ["HEAD CALIBRATION",
                               "Keep your eyes on the centre cross; only turn your head.",
                               "A slight turn is enough - choose a range you can hold comfortably.",
                               "ESC = abort"], H * 0.22)
            return
        if i >= len(self.STEPS):
            return
        key, txt, frac = self.STEPS[i]
        _center_text(img, [f"{i + 1}/{len(self.STEPS)}  {txt}",
                           "HOLD..." if collecting else "get ready"], H * 0.25)
        x = int(frac * (W - 1))
        cv2.arrowedLine(img, (cx, cy + 80), (x if key != "center" else cx, cy + 80),
                        WHITE if collecting else GREY, 4, tipLength=0.05)
