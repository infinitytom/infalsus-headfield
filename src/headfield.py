"""HeadField - slight head turns (webcam, MediaPipe head pose) -> horizontal mouse output.

Experimental prototype for In Falsus Field control. Everything runs locally;
camera frames are never saved or uploaded.

Run:  .venv\\Scripts\\python.exe src\\headfield.py           (main UI)
      ... src\\headfield.py --list-cameras
      ... src\\headfield.py --selftest 8                  (headless timing check)
"""
import argparse
import collections
import csv
import json
import math
import os
import re
import sys
import threading
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winio  # noqa: E402
from headmodel import HeadModel, OneEuro  # noqa: E402
from routines import FollowTest, HeadCalibRoutine, ValidRoutine  # noqa: E402
from tracker import Camera, FaceTracker, camera_names, resolve_camera  # noqa: E402

__version__ = "0.1.1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(ROOT, "config.json")
PROFILE_DIR = os.path.join(ROOT, "profiles")
MODEL_PATH = os.path.join(ROOT, "models", "face_landmarker.task")
LOG_DIR = os.path.join(ROOT, "logs")

DEFAULTS = {
    "camera_name": None,           # chosen by NAME at first start; never by probing indices
    "profile": "default",          # per-person calibration profile
    "head_signal": "yaw",          # "yaw" (turning), "nose_rel" (turning, nose vs cheeks) or "nose_x" (sideways shift)
    "camera_index": None, "cam_width": 640, "cam_height": 480, "cam_fps": 60,
    "cam_exposure": None,          # None = auto; e.g. -6 (16 ms) keeps 30 fps in dim light
    "use_gpu": False,
    "calib_y": 0.5,                # vertical position (0 top..1 bottom) of test targets
    "mincut_x10": 15,              # One Euro min cutoff (Hz*10): lower = smoother, more lag
    "beta_x100": 100,              # One Euro speed coefficient: higher = faster on big moves
    "gain_pct": 100,               # sensitivity around centre
    "curve_x100": 100,             # response curve exponent x100: 100 = linear, >100 = slow near centre, faster at edges
    "offset_pct": 50,              # 50 = no offset
    "out_left_pct": 0, "out_right_pct": 100,   # output clamp range (screen %)
    "deadband_permille": 0,        # ignore changes smaller than this (0.1% of width)
    "rel_scale_x100": 100,         # rel mode: mouse counts per screen pixel
    "output_mode": "abs",          # abs = SendInput absolute move (same mechanism as pydirectinput.moveTo)
    "output_y": 0.5,               # pin cursor height (fraction of screen); null = keep current height
    "always_on_top": True,         # keep the HeadField window above the game (key w toggles)
    "hotkeys": {                   # global hotkeys; change here if another program already uses one
        "toggle_output": "ctrl+alt+m", "out_mode": "ctrl+alt+n", "pause": "ctrl+alt+p",
        "recenter": "ctrl+alt+c", "quit": "ctrl+alt+q"},
}

SLIDERS = [  # (key, max, label)
    ("mincut_x10", 100, "smooth mincut Hz x10"),
    ("beta_x100", 1000, "speed beta x100"),
    ("gain_pct", 300, "gain %"),
    ("curve_x100", 300, "curve x100 (100=linear)"),
    ("offset_pct", 100, "offset (50=0)"),
    ("out_left_pct", 100, "out left %"),
    ("out_right_pct", 100, "out right %"),
    ("deadband_permille", 50, "deadband 0.1%"),
    ("rel_scale_x100", 500, "rel scale x100"),
]

WIN_MAIN, WIN_CTRL, WIN_TGT = "HeadField", "HeadField Controls", "HeadField Target"


def load_cfg():
    cfg = dict(DEFAULTS)
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print("config.json unreadable, using defaults:", e)
    return cfg


def calib_path(profile):
    return os.path.join(PROFILE_DIR, f"{profile}.json")


def list_profiles():
    if not os.path.isdir(PROFILE_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(PROFILE_DIR) if f.endswith(".json"))


def save_cfg(cfg):
    # only known keys; never persist a raw camera index
    out = {k: v for k, v in cfg.items() if k in DEFAULTS and k != "camera_index"}
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


def stamp():
    return time.strftime("%Y%m%d_%H%M%S")


def pstats(dq):
    if not dq:
        return 0.0, 0.0
    a = np.array(list(dq), float)
    return float(a.mean()), float(np.percentile(a, 95))


class App:
    def __init__(self, cfg, headless=False):
        self.cfg = cfg
        self.headless = headless
        winio.set_dpi_aware()
        self.ime_off = winio.disable_ime()   # before any window: Chinese IME must not eat o/c/r...
        self.single = winio.single_instance()
        self.boost = winio.boost_process()
        self.sw, self.sh = winio.screen_size()
        if not os.path.exists(MODEL_PATH):
            raise SystemExit(f"model missing: {MODEL_PATH}\nRun 1_安装依赖.bat first.")
        self.tracker = FaceTracker(MODEL_PATH, use_gpu=cfg["use_gpu"])
        self.cam = None
        self.open_camera()
        self.model = HeadModel(cfg["head_signal"])
        self.calib_note = ""
        cp = calib_path(cfg["profile"])
        if os.path.exists(cp):
            try:
                self.model.load(cp)
            except Exception as e:
                self.calib_note = f"old/invalid calibration ({e}) - recalibrate"
                print("calibration not loaded:", e)
        self.head_vals = collections.deque(maxlen=15)  # recent raw head values for recentering
        self.filt = OneEuro()
        self.mouse = winio.MouseOut(cfg["output_mode"])
        self.mouse_on = False   # default OFF
        self.paused = False
        self.running = True
        self.routine = None
        self.last_valid = None   # ValidRoutine result
        self.last_test = None
        self.msg, self.msg_until = "", 0.0
        self.hist = collections.deque(maxlen=400)  # (t, g_raw, g_filt, out, target)
        self.det_ms = collections.deque(maxlen=120)
        self.proc_ms = collections.deque(maxlen=120)
        self.step_t = collections.deque(maxlen=60)       # tracking-loop timestamps
        self.ui_ms = collections.deque(maxlen=60)
        self.valid_hist = collections.deque(maxlen=60)   # face_ok per frame
        self.pending = None                              # finished routine -> UI thread
        self.moves_sent = 0
        self.last_img = None
        self.lost_since = None
        self.was_outputting = False
        self.held_g = None
        self.state = {}

    # ---------------------------------------------------------------- setup
    def open_camera(self):
        """Open ONLY the camera named in config["camera_name"].
        An explicit index (--camera) is used only if given on the command line."""
        c = self.cfg
        idx = c.get("camera_index")
        if idx is None:
            idx, names = resolve_camera(c["camera_name"])
            if idx is None:
                raise SystemExit(f'camera "{c["camera_name"]}" not found. Devices: {names}\n'
                                 f'Set "camera_name" in config.json to one of these.')
        self.cam_idx = idx
        self.cam = Camera(idx, c["cam_width"], c["cam_height"], c["cam_fps"], c.get("cam_exposure"))
        self.cam.start()

    def flash(self, s, dur=3.0):
        self.msg, self.msg_until = s, time.perf_counter() + dur
        print(s)
        self.log_event(s)

    def log_event(self, s):
        """Session log (text only, no images): helps diagnose 'nothing happens in game'."""
        try:
            if not getattr(self, "_log_path", None):
                os.makedirs(LOG_DIR, exist_ok=True)
                self._log_path = os.path.join(LOG_DIR, f"session_{stamp()}.log")
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%H:%M:%S')} {s}\n")
        except Exception:
            pass

    def _stats_loop(self):
        """Once per second: foreground program, output state, moves actually sent."""
        last = 0
        while self.running:
            time.sleep(1.0)
            st = list(self.step_t)
            tfps = (len(st) - 1) / (st[-1] - st[0]) if len(st) > 2 and st[-1] > st[0] else 0
            sent, last = self.moves_sent - last, self.moves_sent
            vh = list(self.valid_hist)
            self.log_event(f"fg={winio.foreground_exe()} mouse={'ON' if self.mouse_on else 'off'} "
                           f"mode={self.mouse.mode} paused={self.paused} face={sum(vh)}/{len(vh)} "
                           f"moves_sent/s={sent} cam_fps={self.cam.fps():.1f} tracked_fps={tfps:.1f}")

    # hotkey callbacks (run in hotkey thread; plain attribute writes)
    def hk_toggle_mouse(self):
        if not self.mouse_on and not self.model.ready:
            winio.beep(300, 200)
            self.flash("calibrate first (mouse output stays OFF)")
            return
        self.mouse_on = not self.mouse_on
        winio.beep(1400 if self.mouse_on else 600, 70)
        self.flash(f"mouse output {'ON' if self.mouse_on else 'OFF'}")

    def hk_cycle_output(self):
        i = winio.MouseOut.MODES.index(self.mouse.mode)
        self.mouse.mode = winio.MouseOut.MODES[(i + 1) % 3]
        self.mouse.reset()
        self.cfg["output_mode"] = self.mouse.mode
        n = winio.MouseOut.MODES.index(self.mouse.mode) + 1   # 1/2/3 beeps = setpos/abs/rel
        winio.beep_n(1000, n)
        self.flash(f"output mode: {self.mouse.mode}")

    def hk_pause(self):
        self.paused = not self.paused
        winio.beep(500 if self.paused else 1100, 70)
        self.flash("PAUSED" if self.paused else "resumed")

    def hk_quit(self):
        self.running = False

    def hk_recenter(self):
        """Make the current head pose the centre (fixes slow drift)."""
        if not self.model.ready or not self.head_vals:
            winio.beep(300, 150)
            return
        self.model.recenter(float(np.median(self.head_vals)))
        self.filt.reset()
        self.held_g = None
        winio.beep(900, 50)
        self.flash("re-centred")

    # ---------------------------------------------------------------- per frame
    def params(self):
        c = self.cfg
        return dict(mincut=max(c["mincut_x10"], 1) / 10.0, beta=c["beta_x100"] / 100.0,
                    gain=c["gain_pct"] / 100.0, offset=(c["offset_pct"] - 50) / 100.0,
                    curve=max(c["curve_x100"], 30) / 100.0,
                    out_l=c["out_left_pct"] / 100.0, out_r=max(c["out_right_pct"], c["out_left_pct"] + 1) / 100.0,
                    deadband=c["deadband_permille"] / 1000.0,
                    rel_scale=max(c["rel_scale_x100"], 1) / 100.0)

    def step(self, img, t_frame):
        p = self.params()
        self.filt.min_cutoff, self.filt.beta = p["mincut"], p["beta"]
        t0 = time.perf_counter()
        f = self.tracker.process(img)
        t1 = time.perf_counter()
        now = t1

        # --- validity: a face must be found (blinking does not affect head pose)
        valid = f.ok
        if f.ok:
            self.lost_since = None
            self.head_vals.append(HeadModel.value(f, self.model.signal))
        else:
            if self.lost_since is None:
                self.lost_since = now
            if now - self.lost_since > 0.3:
                self.filt.reset()
                self.held_g = None

        g_raw = g_f = out = float("nan")
        if valid and self.model.ready:
            g_raw = self.model.predict(f)
            g_f = self.filt(g_raw, t_frame)
            if self.held_g is None or abs(g_f - self.held_g) > p["deadband"]:
                self.held_g = g_f
            g_use = self.held_g
            # response curve: d in [-0.5, 0.5] around centre; |2d|^curve keeps the edges fixed
            d = g_use - 0.5
            if p["curve"] != 1.0:
                d = math.copysign(min(abs(2 * d), 4.0) ** p["curve"] / 2, d)
            out = 0.5 + p["offset"] + p["gain"] * d
            out = min(max(out, p["out_l"]), p["out_r"])

        outputting = (self.mouse_on and not self.paused and valid and self.model.ready
                      and self.routine is None and not math.isnan(out))
        if outputting:
            if not self.was_outputting:
                self.mouse.reset()
            self.mouse.move_to(out, self.sw, self.sh, p["rel_scale"], self.cfg.get("output_y"))
            self.moves_sent += 1
        self.was_outputting = outputting
        t2 = time.perf_counter()

        self.det_ms.append((t1 - t0) * 1000)
        self.proc_ms.append((t2 - t_frame) * 1000)
        self.step_t.append(t2)
        self.valid_hist.append(f.ok)

        tgt = None
        r = self.routine
        if r is not None:
            r.update(now, valid, f, g_raw, g_f)
            tgt = r.target(now) if not r.done else None
            if r.done:
                self.routine = None
                self.pending = r      # file writing / window closing happen in UI thread
        self.hist.append((now, g_raw, g_f, out, tgt if tgt is not None else float("nan")))
        self.state = dict(f=f, valid=valid, g_raw=g_raw, g_f=g_f,
                          out=out, outputting=outputting, now=now)

    # ---------------------------------------------------------------- routines
    def start_routine(self, kind):
        y = self.cfg["calib_y"]
        if kind != "calib" and not self.model.ready:
            self.flash("calibrate first (press c)")
            return
        if kind == "calib":
            self.routine = HeadCalibRoutine(HeadModel(self.cfg["head_signal"]))
        elif kind == "valid":
            self.routine = ValidRoutine(y)
        else:
            self.routine = FollowTest(y, True)
        self.routine.kind = kind
        if not self.headless:
            cv2.namedWindow(WIN_TGT, cv2.WINDOW_NORMAL)
            cv2.setWindowProperty(WIN_TGT, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
            try:
                cv2.setWindowProperty(WIN_TGT, cv2.WND_PROP_TOPMOST, 1)
            except Exception:
                pass

    def abort_routine(self):
        self.routine = None
        self.close_target()
        self.flash("aborted")

    def close_target(self):
        try:
            cv2.destroyWindow(WIN_TGT)
        except Exception:
            pass

    def finish_routine(self, r):
        kind = r.kind
        self.close_target()
        os.makedirs(LOG_DIR, exist_ok=True)
        res = r.result or {"ok": False}
        if kind == "calib":
            if res.get("ok"):
                self.model = r.model
                os.makedirs(PROFILE_DIR, exist_ok=True)
                r.model.save(calib_path(self.cfg["profile"]))
                self.calib_note = ""
                self.filt.reset()
                self.held_g = None
                self.flash(f"calibrated: head turn range {abs(res['span_yaw_deg']):.1f} deg", 6)
            else:
                self.flash("calibration failed: " + res.get("error", "?"), 6)
        elif kind == "valid":
            self.last_valid = res
            if res.get("ok"):
                self.flash(f"validation: MAE {res['mae_frac'] * 100:.1f}% width, "
                           f"jitter raw {res['jitter_raw_frac'] * 100:.2f}% / filt {res['jitter_filt_frac'] * 100:.2f}%", 6)
        else:
            self.last_test = res
            name = os.path.join(LOG_DIR, f"test_{stamp()}.csv")
            with open(name, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["t_s", "phase", "target", "pos_raw", "pos_filt", "valid", "face", "yaw_deg"])
                w.writerows(r.log)
            self.flash("follow test done, numbers saved to logs/", 6)
        st = list(self.step_t)
        res["camera_fps"] = round(self.cam.fps(), 1)
        res["tracked_fps"] = round((len(st) - 1) / (st[-1] - st[0]), 1) if len(st) > 2 and st[-1] > st[0] else None
        res["cam_exposure"] = self.cfg.get("cam_exposure")
        res["profile"] = self.cfg["profile"]
        res["screen_px"] = [self.sw, self.sh]
        res["params"] = {k: self.cfg[k] for k, _, _ in SLIDERS}
        with open(os.path.join(LOG_DIR, f"{kind}_{stamp()}.json"), "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)

    # ---------------------------------------------------------------- drawing
    def draw_main(self, img):
        W, H = 1000, 660
        cv = np.full((H, W, 3), 24, np.uint8)
        s = self.state
        f = s.get("f")
        # camera preview (mirrored), a few face points only
        ph, pw = 270, 360
        prev = cv2.resize(img, (pw, ph))
        if f is not None and f.ok:
            sx, sy = pw / img.shape[1], ph / img.shape[0]
            for k, (x, y) in f.pts.items():
                col = (0, 255, 255) if k == 1 else (0, 200, 0)
                cv2.circle(prev, (int(x * sx), int(y * sy)), 4 if k == 1 else 2, col, -1)
        cv[10:10 + ph, 10:10 + pw] = cv2.flip(prev, 1)

        def T(line, y, col=(220, 220, 220)):
            cv2.putText(cv, line, (385, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)

        dm, dp = pstats(self.det_ms)
        pm, pp = pstats(self.proc_ms)
        um, _ = pstats(self.ui_ms)
        fps = self.cam.fps() if self.cam else 0
        st = list(self.step_t)
        tfps = (len(st) - 1) / (st[-1] - st[0]) if len(st) > 2 and st[-1] > st[0] else 0
        vh = list(self.valid_hist)
        nf = sum(1 for o in vh if not o)
        y = 26
        lines = [
            (f"HeadField {__version__}   profile: {self.cfg['profile']}   camera: {self.cfg['camera_name']} {self.cam.size[0]}x{self.cam.size[1]}", None),
            (f"camera {fps:.1f} fps | tracked {tfps:.1f} fps | UI {um:.0f} ms | exposure "
             f"{self.cfg.get('cam_exposure') if self.cfg.get('cam_exposure') is not None else 'auto'}",
             (60, 60, 255) if (fps and tfps < 0.8 * fps) or fps < 20 else None),
            (f"detect {dm:.1f} ms avg / {dp:.1f} p95", None),
            (f"processing {pm:.1f} ms avg / {pp:.1f} p95  (frame received -> output)", None),
            ("  = processing time only; NOT end-to-end latency", (140, 140, 140)),
            ("  (excludes exposure, USB, display, game input)", (140, 140, 140)),
        ]
        if f is not None and f.ok:
            lines.append((f"FACE ok  yaw {f.yaw:+.1f} deg  pitch {f.pitch:+.1f}  nose x {f.nose_x:.3f}"
                          f"   last 2s: no-face {nf}/{len(vh)}",
                          (80, 220, 80) if nf < 0.2 * max(len(vh), 1) else (0, 160, 255)))
        elif img.mean() < 3:
            lines.append(("CAMERA FRAME IS BLACK: lens shutter / camera privacy key?", (60, 60, 255)))
        else:
            lines.append(("FACE LOST -> output held", (60, 60, 255)))
        if self.model.ready:
            hm = self.model
            p_ = hm.pts[hm.signal]
            cur = HeadModel.value(f, hm.signal) if f is not None and f.ok else float("nan")
            lines.append((f"calib ({hm.signal}): left {p_['left']:.3f}  centre {p_['center']:.3f}  "
                          f"right {p_['right']:.3f}   now {cur:.3f}", None))
        else:
            lines.append(("NOT CALIBRATED - press c   " + self.calib_note[:60], (0, 160, 255)))
        if self.last_valid and self.last_valid.get("ok"):
            v = self.last_valid
            lines.append((f"valid: MAE {v['mae_frac'] * 100:.1f}%  jitter raw {v['jitter_raw_frac'] * 100:.2f}% "
                          f"filt {v['jitter_filt_frac'] * 100:.2f}%", None))
        # live jitter over last 0.5 s
        now = s.get("now", 0)
        hist = list(self.hist)
        rec = [h for h in hist if now - h[0] < 0.5 and not math.isnan(h[2])]
        if len(rec) > 5:
            jr = np.std([h[1] for h in rec]) * self.sw
            jf = np.std([h[2] for h in rec]) * self.sw
            lines.append((f"live spread 0.5s: raw {jr:.0f}px  filt {jf:.0f}px  (jitter only while holding still)", None))
        mo = "ON" if self.mouse_on else "OFF"
        lines.append((f"MOUSE {mo} [{self.mouse.mode}]  {'PAUSED' if self.paused else ''}  "
                      f"{'-> sending' if s.get('outputting') else ''}",
                      (80, 220, 80) if self.mouse_on else (180, 180, 180)))
        if self.paused:
            lines.append(("PAUSED (Ctrl+Alt+P)", (0, 160, 255)))
        for txt, col in lines:
            T(txt, y, col or (220, 220, 220))
            y += 21
        if time.perf_counter() < self.msg_until:
            cv2.putText(cv, self.msg, (10, 305), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 255), 2, cv2.LINE_AA)

        # bar: whole screen width
        p = self.params()
        bx0, bx1, by = 40, 960, 340
        bw = bx1 - bx0
        cv2.rectangle(cv, (bx0 + int(p["out_l"] * bw), by - 14), (bx0 + int(p["out_r"] * bw), by + 14), (50, 70, 50), -1)
        cv2.rectangle(cv, (bx0, by - 14), (bx1, by + 14), (120, 120, 120), 1)

        def mark(v, col, r):
            if v is not None and not math.isnan(v):
                cv2.circle(cv, (bx0 + int(min(max(v, -0.05), 1.05) * bw), by), r, col, -1)
        if hist:
            h = hist[-1]
            mark(h[4], (60, 60, 255), 9)
            mark(h[1], (150, 150, 150), 4)
            mark(h[2], (255, 220, 0), 7)
            mark(h[3], (80, 220, 80), 4)
        cv2.putText(cv, "screen width:  grey=raw  cyan=filtered  green=output  red=target  dark-green=output range",
                    (bx0, by + 34), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (160, 160, 160), 1, cv2.LINE_AA)

        # time plot last 4 s (x horizontal = time, y = screen fraction)
        px0, py0, px1, py1 = 40, 390, 960, 600
        cv2.rectangle(cv, (px0, py0), (px1, py1), (70, 70, 70), 1)
        span = 4.0
        for idx, col in ((4, (60, 60, 255)), (1, (110, 110, 110)), (2, (255, 220, 0)), (3, (80, 220, 80))):
            pts = []
            for hh in hist:
                v = hh[idx]
                if now - hh[0] > span or math.isnan(v):
                    if len(pts) > 1:
                        cv2.polylines(cv, [np.array(pts, np.int32)], False, col, 1, cv2.LINE_AA)
                    pts = []
                    continue
                xx = px1 - (now - hh[0]) / span * (px1 - px0)
                yy = py1 - min(max(v, 0), 1) * (py1 - py0)
                pts.append((xx, yy))
            if len(pts) > 1:
                cv2.polylines(cv, [np.array(pts, np.int32)], False, col, 1, cv2.LINE_AA)
        cv2.putText(cv, "last 4 s (up = right side of screen)", (px0, py0 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                    (160, 160, 160), 1, cv2.LINE_AA)
        help_ = ("keys: c calibrate  v validate  t test  r re-centre  o mouse  m out-mode  "
                 "e camera  w on-top  s save  q quit")
        cv2.putText(cv, help_, (10, 625), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(cv, "global: " + getattr(self, "hotkey_text", ""),
                    (10, 648), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1, cv2.LINE_AA)
        cv2.imshow(WIN_MAIN, cv)

    def draw_target(self):
        r = self.routine
        if r is None or getattr(r, "t0", None) is None:
            return
        H, W = self.sh // 2, self.sw // 2
        canvas = np.zeros((H, W, 3), np.uint8)
        est = self.state.get("g_f")
        r.draw(canvas, time.perf_counter(), est)
        cv2.imshow(WIN_TGT, canvas)

    # ---------------------------------------------------------------- UI
    def make_controls(self):
        cv2.namedWindow(WIN_MAIN, cv2.WINDOW_AUTOSIZE)
        cv2.namedWindow(WIN_CTRL, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WIN_CTRL, 520, 420)
        for key, mx, label in SLIDERS:
            cv2.createTrackbar(label, WIN_CTRL, int(self.cfg[key]), mx, lambda v: None)
        for w in (WIN_MAIN, WIN_CTRL):
            winio.no_ime_window(w)
        self._top_t = 0.0

    def keep_on_top(self, force=False):
        """Re-assert 'always on top' every 2 s (a game going fullscreen can push it down)."""
        now = time.perf_counter()
        if force or now - self._top_t > 2.0:
            self._top_t = now
            winio.set_topmost(WIN_MAIN, bool(self.cfg.get("always_on_top")))

    def read_controls(self):
        for key, _, label in SLIDERS:
            try:
                self.cfg[key] = cv2.getTrackbarPos(label, WIN_CTRL)
            except cv2.error:
                pass

    def handle_key(self, k):
        if k < 0:
            return
        k &= 0xFF
        if k == 27:
            if self.routine is not None:
                self.abort_routine()
        elif self.routine is not None:
            return
        elif k == ord("q"):
            self.running = False
        elif k == ord("c"):
            self.start_routine("calib")
        elif k == ord("v"):
            self.start_routine("valid")
        elif k == ord("t"):
            self.start_routine("test")
        elif k == ord("o"):
            self.hk_toggle_mouse()
        elif k == ord("p"):
            self.hk_pause()
        elif k == ord("m"):
            self.hk_cycle_output()
        elif k == ord("e"):
            self.cam.open_settings()
        elif k == ord("r"):
            self.hk_recenter()
        elif k == ord("w"):
            self.cfg["always_on_top"] = not self.cfg.get("always_on_top")
            self.keep_on_top(force=True)
            self.flash(f"always on top {'ON' if self.cfg['always_on_top'] else 'OFF'}")
        elif k == ord("s"):
            save_cfg(self.cfg)
            self.flash("settings saved")

    def run(self):
        actions = {"toggle_output": self.hk_toggle_mouse, "out_mode": self.hk_cycle_output,
                   "pause": self.hk_pause, "recenter": self.hk_recenter, "quit": self.hk_quit}
        hk_cfg = {**DEFAULTS["hotkeys"], **(self.cfg.get("hotkeys") or {})}
        binds, names = [], {}
        for act, cb in actions.items():
            try:
                mods, vk = winio.parse_hotkey(hk_cfg[act])
            except ValueError as e:
                self.flash(f"bad hotkey in config.json: {e}", 8)
                continue
            binds.append((mods, vk, cb))
            names[(mods, vk)] = hk_cfg[act]
        hk = winio.GlobalHotkeys(binds)
        hk.start()
        hk.ready.wait(2)
        self.hotkey_text = " | ".join(f"{hk_cfg[a]} {a}" for a in actions)
        lost = [k for k in hk.failed if k not in hk.hooked]
        if hk.hooked:
            via = ", ".join(names.get(k, str(k)) for k in hk.hooked)
            self.flash(f"note: {via} also used by another program -> handled via keyboard hook "
                       f"(the other program may react too; change 'hotkeys' in config.json if so)", 10)
        if lost:
            taken = ", ".join(names.get(k, str(k)) for k in lost)
            self.flash(f"WARNING: hotkey(s) not available: {taken} "
                       f"(change 'hotkeys' in config.json; is an old HeadField still running?)", 12)
        self.make_controls()
        self.keep_on_top(force=True)
        worker = threading.Thread(target=self._track_loop, daemon=True)
        worker.start()
        threading.Thread(target=self._stats_loop, daemon=True).start()
        self.log_event(f"start: HeadField {__version__} camera={self.cfg['camera_name']} profile={self.cfg['profile']} "
                       f"signal={self.cfg['head_signal']} boost={self.boost} hotkeys_failed={hk.failed} "
                       f"hotkeys_hooked={hk.hooked} ime_off={self.ime_off} "
                       f"other_instance={not self.single}")
        try:
            # UI thread: draw at most ~30 Hz; tracking/mouse output never wait for drawing
            while self.running:
                t0 = time.perf_counter()
                self.read_controls()
                if self.pending is not None:
                    r, self.pending = self.pending, None
                    self.finish_routine(r)
                if self.last_img is not None and self.state:
                    self.draw_main(self.last_img)
                    self.draw_target()
                self.keep_on_top()
                self.ui_ms.append((time.perf_counter() - t0) * 1000)
                self.handle_key(cv2.waitKey(max(1, int(33 - (time.perf_counter() - t0) * 1000))))
                if cv2.getWindowProperty(WIN_MAIN, cv2.WND_PROP_VISIBLE) < 1:
                    break
        finally:
            self.running = False
            worker.join(timeout=2)
            hk.stop()
            self.cam.stop()
            self.tracker.close()
            cv2.destroyAllWindows()
            save_cfg(self.cfg)

    def _track_loop(self):
        last_seq = 0
        while self.running:
            got = self.cam.get(last_seq, timeout=1.0)
            if got is None:
                self.flash("no frames from camera")
                continue
            last_seq, img, t_frame = got
            try:
                self.step(img, t_frame)
            except Exception as e:  # keep tracking alive; show the error
                self.flash(f"tracking error: {e}")
            self.last_img = img

    # ---------------------------------------------------------------- selftest
    def selftest(self, seconds):
        last_seq, n, n_face = 0, 0, 0
        yaws = []
        t_end = time.perf_counter() + seconds
        while time.perf_counter() < t_end:
            got = self.cam.get(last_seq, timeout=1.0)
            if got is None:
                continue
            last_seq, img, t_frame = got
            self.step(img, t_frame)
            n += 1
            f = self.state["f"]
            if f.ok:
                n_face += 1
                yaws.append(f.yaw)
        fps = self.cam.fps()
        dm, dp = pstats(self.det_ms)
        pm, pp = pstats(self.proc_ms)
        rep = {
            "camera_name": self.cfg["camera_name"], "camera_index": self.cam_idx, "camera_size": self.cam.size,
            "camera_fps": round(fps, 1), "frames_processed": n,
            "face_detected_ratio": round(n_face / max(n, 1), 3),
            "tracked_fps": round(n / seconds, 1),
            "detect_ms_avg_p95": [round(dm, 2), round(dp, 2)],
            "processing_ms_avg_p95": [round(pm, 2), round(pp, 2)],
            "yaw_std_deg": round(float(np.std(yaws)), 2) if yaws else None,
            "yaw_median_deg": round(float(np.median(yaws)), 1) if yaws else None,
            "screen_px": [self.sw, self.sh],
            "note": "processing time = frame received -> output computed; not end-to-end latency",
        }
        self.cam.stop()
        self.tracker.close()
        return rep


VIRTUAL_HINT = re.compile(r"virtual|obs|虚拟|组合|前置|后置|phone|手机|droidcam|iriun|continuity|ndi|snap|xsplit",
                          re.I)


def list_cameras():
    """Names only; does not open any device."""
    names = camera_names()
    for i, n in enumerate(names):
        tag = "   <- 可能是虚拟/手机摄像头" if VIRTUAL_HINT.search(n) else ""
        print(f"  [{i}] {n}{tag}")
    return names


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def choose_camera(cfg, force=False):
    """Interactive, name-based camera choice. Only the chosen device is ever opened."""
    names = camera_names()
    if not names:
        raise SystemExit("没有找到任何摄像头设备。")
    if not force and cfg.get("camera_name") in names:
        return
    print("\n可用摄像头（此时没有打开任何摄像头）：")
    list_cameras()
    real = [i for i, n in enumerate(names) if not VIRTUAL_HINT.search(n)]
    sug = real[0] if real else 0
    while True:
        a = ask(f"请选择要使用的摄像头编号 [回车 = {sug}: {names[sug]}]: ")
        if a == "":
            i = sug
        elif a.isdigit() and int(a) < len(names):
            i = int(a)
        else:
            print("输入无效。")
            continue
        if VIRTUAL_HINT.search(names[i]):
            if ask(f"“{names[i]}” 看起来是虚拟/手机摄像头，打开它可能会启动其他设备。确定使用？(y/N): ").lower() != "y":
                continue
        cfg["camera_name"] = names[i]
        save_cfg(cfg)
        print(f"已选择：{names[i]}（保存在 config.json，可用 --choose-camera 重新选择）")
        return


def choose_profile(cfg):
    profs = list_profiles()
    last = cfg.get("profile", "default")
    print("\n使用者档案（每个人的校准单独保存）：", "、".join(profs) if profs else "（还没有）")
    a = ask(f"输入使用者名称 [回车 = {last}]: ")
    if a:
        a = re.sub(r'[\\/:*?"<>|]', "_", a)[:40]
        cfg["profile"] = a
    save_cfg(cfg)
    has = os.path.exists(calib_path(cfg["profile"]))
    print(f"档案：{cfg['profile']}  {'（已有校准）' if has else '（新档案，需要按 c 校准）'}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", type=int, help="force a DSHOW index (overrides camera_name)")
    ap.add_argument("--choose-camera", action="store_true", help="pick the camera again")
    ap.add_argument("--profile", help="user profile name (per-person calibration)")
    ap.add_argument("--no-prompt", action="store_true", help="skip the profile question")
    ap.add_argument("--list-cameras", action="store_true")
    ap.add_argument("--selftest", type=float, metavar="SECONDS")
    ap.add_argument("--report", help="write selftest/list output to this file")
    a = ap.parse_args()
    cfg = load_cfg()
    if a.camera is not None:
        cfg["camera_index"] = a.camera

    if a.list_cameras:
        names = list_cameras()
        if a.report:
            with open(a.report, "w", encoding="utf-8") as fh:
                json.dump(names, fh, ensure_ascii=False, indent=1)
        return

    if a.selftest:
        if not cfg.get("camera_name") and cfg.get("camera_index") is None:
            raise SystemExit("no camera chosen yet: start the program once and pick a camera")
        if a.profile:
            cfg["profile"] = a.profile
        app = App(cfg, headless=True)
        rep = app.selftest(a.selftest)
        s = json.dumps(rep, ensure_ascii=False, indent=2)
        print(s)
        if a.report:
            with open(a.report, "w", encoding="utf-8") as fh:
                fh.write(s)
        return

    if not winio.single_instance("Local\\HeadFieldLauncher"):
        print("\n注意：已经有一个 HeadField 正在运行。两个同时运行会互相抢鼠标和快捷键，"
              "请先关闭另一个（任务栏里的 HeadField 窗口，或在它的窗口里按 q）。")
        if ask("仍然继续启动？(y/N): ").lower() != "y":
            return
    if cfg.get("camera_index") is None:
        choose_camera(cfg, force=a.choose_camera)
    if a.profile:
        cfg["profile"] = a.profile
    elif not a.no_prompt:
        choose_profile(cfg)
    App(cfg).run()


if __name__ == "__main__":
    main()
