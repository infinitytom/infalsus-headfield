"""Frame-rate check for the configured camera only (config.json camera_name).
Tries auto exposure and a few fixed exposures; reports camera fps, tracked fps,
brightness and face ratio. Numbers only, no images saved. Restores auto exposure."""
import json, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from tracker import Camera, FaceTracker, resolve_camera
cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
idx, names = resolve_camera(cfg["camera_name"])
if idx is None:
    sys.exit(f"camera {cfg['camera_name']!r} not found in {names}")
trk = FaceTracker(os.path.join(ROOT, "models", "face_landmarker.task"))
out = []
for exp in (None, -5, -6, -7, None):
    cam = Camera(idx, cfg.get("cam_width", 640), cfg.get("cam_height", 480), cfg.get("cam_fps", 60), exp)
    cam.start()
    time.sleep(1.0)  # let exposure settle
    seq, n, faces, bright, t0 = 0, 0, 0, [], time.perf_counter()
    while time.perf_counter() - t0 < 4.0:
        got = cam.get(seq, 1.0)
        if not got:
            continue
        seq, img, _ = got
        n += 1
        bright.append(float(img.mean()))
        faces += trk.process(img).ok
    r = {"exposure": exp if exp is not None else "auto", "camera_fps": round(cam.fps(), 1),
         "tracked_fps": round(n / 4.0, 1), "brightness": round(sum(bright) / max(len(bright), 1), 1),
         "face_ratio": round(faces / max(n, 1), 2)}
    cam.stop()
    out.append(r)
    print(r, flush=True)
json.dump(out, open(os.path.join(ROOT, "logs", "fps_check.json"), "w"), indent=1)
