"""Mouse-injection diagnostic (no camera). Phase 1 on the desktop, phase 2 in the
game window. For each injection mode it sweeps the cursor horizontally for 2 s
and records: SendInput return values, whether GetCursorPos actually changed,
the foreground process and whether it runs elevated (UIPI blocks injection from
a non-elevated process into an elevated one). Writes logs/input_probe.json."""
import ctypes, json, math, os, sys, time
from ctypes import wintypes
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _hook(t, v, tb):  # record crashes that happen even before run()
    import traceback
    txt = "".join(traceback.format_exception(t, v, tb))
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    open(os.path.join(ROOT, "logs", "input_probe_error.txt"), "w", encoding="utf-8").write(txt)
    sys.__excepthook__(t, v, tb)


sys.excepthook = _hook
sys.path.insert(0, os.path.join(ROOT, "src"))
import winio
from winio import INPUT, MOUSEINPUT, _U, MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE

_LOG = os.path.join(ROOT, "logs", "input_probe_log.txt")
os.makedirs(os.path.dirname(_LOG), exist_ok=True)
open(_LOG, "w", encoding="utf-8").close()
_print = print


def print(*a, **k):  # noqa: A001 - tee console output into a log file
    _print(*a, **k)
    with open(_LOG, "a", encoding="utf-8") as fh:
        fh.write(" ".join(str(x) for x in a) + "\n")


u32, k32, adv = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.advapi32
winio.set_dpi_aware()
SW, SH = winio.screen_size()


def is_self_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None


def fg_info():
    hwnd = u32.GetForegroundWindow()
    title = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(hwnd, title, 256)
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    info = {"title": title.value, "pid": pid.value, "exe": None, "elevated": None}
    h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            info["exe"] = os.path.basename(buf.value)
        tok = wintypes.HANDLE()
        if adv.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):  # TOKEN_QUERY
            elev = wintypes.DWORD()
            ret = wintypes.DWORD()
            if adv.GetTokenInformation(tok, 20, ctypes.byref(elev), 4, ctypes.byref(ret)):  # TokenElevation
                info["elevated"] = bool(elev.value)
            k32.CloseHandle(tok)
        else:
            info["elevated"] = "unknown (token access denied -> probably elevated)"
        k32.CloseHandle(h)
    # clip rectangle (games often confine the cursor)
    r = wintypes.RECT()
    u32.GetClipCursor(ctypes.byref(r))
    info["clip_rect"] = [r.left, r.top, r.right, r.bottom]
    info["cursor_visible_hint"] = None
    return info


def send(dx, dy, flags):
    inp = INPUT(type=0, u=_U(mi=MOUSEINPUT(dx=int(dx), dy=int(dy), mouseData=0, dwFlags=flags, time=0, dwExtraInfo=0)))
    return u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


MODES = ("setpos", "abs", "rel", "abs_mid", "abs_low")  # abs_mid = what pydirectinput.moveTo does


def sweep(mode, secs=2.0):
    """Move horizontally around the screen centre; measure the real cursor."""
    x0, y0 = winio.cursor_pos()
    sent_ok = sent_fail = 0
    xs = []
    prev_x = None
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < secs:
        t = time.perf_counter() - t0
        frac = 0.5 + 0.25 * math.sin(2 * math.pi * 0.75 * t)
        x = frac * (SW - 1)
        _, y = winio.cursor_pos()
        if mode == "abs_mid":
            y = SH // 2
        elif mode == "abs_low":
            y = int(SH * 0.8)
        if mode == "setpos":
            ok = u32.SetCursorPos(int(x), y)
        elif mode in ("abs", "abs_mid", "abs_low"):
            ok = send(round(x * 65535 / (SW - 1)), round(y * 65535 / (SH - 1)), MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)
        else:
            d = 0 if prev_x is None else int(round(x - prev_x))
            ok = send(d, 0, MOUSEEVENTF_MOVE) if d else 1
        prev_x = x
        sent_ok += bool(ok)
        sent_fail += not ok
        time.sleep(0.01)
        xs.append(winio.cursor_pos()[0])
    span = (max(xs) - min(xs)) if xs else 0
    return {"mode": mode, "calls_ok": sent_ok, "calls_failed": sent_fail,
            "cursor_x_span_px": span, "cursor_moved": span > SW * 0.05,
            "start": [x0, y0], "end": list(winio.cursor_pos())}


def phase(name):
    res = {"phase": name, "foreground": fg_info(), "sweeps": []}
    for i, m in enumerate(MODES):
        winio.beep_n(1000, i + 1)
        time.sleep(0.8)
        res["sweeps"].append(sweep(m))
        time.sleep(0.4)
    res["foreground_after"] = fg_info()
    return res


def run():
    print("=== HeadField 鼠标注入诊断（不使用摄像头）===")
    print(f"本程序以管理员权限运行: {is_self_admin()}   屏幕: {SW}x{SH}")
    print("第 1 步：桌面测试。3 秒后光标会左右摆动 5 次（响 1~5 声 = setpos / abs / rel / abs+纵坐标居中 / abs+纵坐标偏下）。")
    time.sleep(3)
    out = {"self_admin": is_self_admin(), "screen": [SW, SH], "phases": [phase("desktop")]}
    print("第 2 步：请在 15 秒内切换到 In Falsus，并开始一首简单的歌（Field 只有在游玩中才会动）。之后同样摆动 5 次，请记下第几声时 Field 动了。")
    for s in range(15, 0, -1):
        print(f"  {s} ...", flush=True)
        time.sleep(1)
    winio.beep(1500, 200)
    time.sleep(0.5)
    out["phases"].append(phase("game"))
    winio.beep(600, 300)
    p = os.path.join(ROOT, "logs", "input_probe.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("\n结果已保存到 logs\\input_probe.json。请留意第 2 步时游戏里的 Field / 光标有没有动。")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        open(os.path.join(ROOT, "logs", "input_probe_error.txt"), "w", encoding="utf-8").write(tb)
