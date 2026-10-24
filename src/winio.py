"""Windows-only helpers: DPI awareness, mouse injection, global hotkeys, beeps.
On non-Windows platforms everything degrades to no-ops (for offline testing)."""
import ctypes
import sys
import threading
import time

IS_WIN = sys.platform == "win32"

if IS_WIN:
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32


def set_dpi_aware():
    """Work in physical pixels, so mouse coordinates are right on high-DPI (scaled) displays."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def boost_process():
    """Keep tracking fast when the game is in the foreground:
    raise priority and opt out of Windows power throttling (EcoQoS),
    which can move background processes to slow efficiency cores."""
    if not IS_WIN:
        return []
    done = []
    # 64-bit handles: declare types, otherwise ctypes truncates them to 32-bit ints
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    try:
        if kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x8000):  # ABOVE_NORMAL
            done.append("priority")
    except Exception:
        pass
    try:
        class PPTS(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong),
                        ("StateMask", ctypes.c_ulong)]
        st = PPTS(1, 0x1, 0x0)  # EXECUTION_SPEED controlled, throttling OFF
        if kernel32.SetProcessInformation(kernel32.GetCurrentProcess(), 4, ctypes.byref(st), ctypes.sizeof(st)):
            done.append("no-ecoqos")
    except Exception:
        pass
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
        done.append("1ms-timer")
    except Exception:
        pass
    return done


def single_instance(name="Local\\HeadField"):
    """True if this is the only running HeadField (named mutex). The handle is
    kept for the life of the process."""
    if not IS_WIN:
        return True
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    global _MUTEX
    _MUTEX = kernel32.CreateMutexW(None, False, name)
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


_MUTEX = None

_VK_NAMED = {f"f{i}": 0x6F + i for i in range(1, 25)}
_VK_NAMED.update({"space": 0x20, "home": 0x24, "end": 0x23, "insert": 0x2D, "pause": 0x13})
_MODS = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}


def parse_hotkey(text):
    """'ctrl+alt+m' -> (mods, vk). Letters, digits, F1-F24 and a few named keys."""
    mods, vk = 0, None
    for part in text.lower().replace(" ", "").split("+"):
        if part in _MODS:
            mods |= _MODS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part in _VK_NAMED:
            vk = _VK_NAMED[part]
        else:
            raise ValueError(f"unknown key '{part}' in hotkey '{text}'")
    if vk is None:
        raise ValueError(f"hotkey '{text}' has no key")
    return mods, vk


def screen_size():
    if IS_WIN:
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return 1920, 1080


# --------------------------------------------------------------------------- mouse
if IS_WIN:
    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort),
                    ("wParamH", ctypes.c_ushort)]

    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("u", _U)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                    ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000


def _send_mouse(dx, dy, flags):
    inp = INPUT(type=0, u=_U(mi=MOUSEINPUT(dx=int(dx), dy=int(dy), mouseData=0,
                                          dwFlags=flags, time=0, dwExtraInfo=0)))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def cursor_pos():
    if not IS_WIN:
        return 0, 0
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


class MouseOut:
    """Horizontal-only mouse output. Three injection modes, because which one a
    game accepts must be tested:
      setpos : SetCursorPos(x, current y)           (absolute, no input event)
      abs    : SendInput absolute move               (absolute, generates event)
      rel    : SendInput relative move (dx only)     (what raw-input games read)
    """
    MODES = ("setpos", "abs", "rel")

    def __init__(self, mode="setpos"):
        self.mode = mode
        self.prev_px = None
        self.rel_rem = 0.0

    def reset(self):
        self.prev_px = None
        self.rel_rem = 0.0

    def move_to(self, frac, screen_w, screen_h, rel_scale=1.0, y_frac=None):
        """y_frac: None keeps the cursor's current height; a number pins it
        (pydirectinput.moveTo-style tools pin it to the screen centre)."""
        if not IS_WIN:
            return
        x = frac * (screen_w - 1)
        _, y = cursor_pos()
        if y_frac is not None:
            y = int(y_frac * (screen_h - 1))
        if self.mode == "setpos":
            user32.SetCursorPos(int(round(x)), y)
        elif self.mode == "abs":
            _send_mouse(round(x * 65535 / (screen_w - 1)),
                        round(y * 65535 / (screen_h - 1)),
                        MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)
        else:  # rel: send the change since last output, scaled
            if self.prev_px is not None:
                d = (x - self.prev_px) * rel_scale + self.rel_rem
                di = int(round(d))
                self.rel_rem = d - di
                if di:
                    _send_mouse(di, 0, MOUSEEVENTF_MOVE)
        self.prev_px = x


# --------------------------------------------------------------------------- hotkeys
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012


def _call_safe(cb):
    try:
        cb()
    except Exception as e:  # never kill the hotkey/hook thread
        print("hotkey callback error:", e)


class GlobalHotkeys(threading.Thread):
    """Global hotkeys that work while the game has focus.
    First tries RegisterHotKey. If another program already owns a combination
    (RegisterHotKey fails), that combination falls back to a low-level keyboard
    hook, which sees the keys anyway and does not depend on the input method.
    bindings: list of (mods, vk, callback)."""

    def __init__(self, bindings):
        super().__init__(daemon=True)
        self.bindings = bindings
        self.tid = None
        self.failed = []    # RegisterHotKey refused (taken by another program)
        self.hooked = []    # ...of those, handled by the keyboard hook instead
        self.ready = threading.Event()
        self._proc = None   # keep the hook callback alive

    def _install_hook(self, binds):
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.CallNextHookEx.restype = LRESULT
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        table = {}
        for mods, vk, cb in binds:
            table.setdefault(vk, []).append((mods, cb))
        down = set()

        def held(v):
            return user32.GetAsyncKeyState(v) & 0x8000

        def cur_mods():
            m = 0
            if held(0x11):
                m |= MOD_CONTROL
            if held(0x12):
                m |= MOD_ALT
            if held(0x10):
                m |= MOD_SHIFT
            if held(0x5B) or held(0x5C):
                m |= 0x8
            return m

        def proc(n, wp, lp):
            try:
                if n == 0:
                    vk = ctypes.cast(lp, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
                    if wp in (0x100, 0x104):          # WM_KEYDOWN / WM_SYSKEYDOWN
                        if vk in table and vk not in down:
                            down.add(vk)
                            m = cur_mods()
                            for mods, cb in table[vk]:
                                if m == mods:      # run outside the hook: hooks must return fast
                                    threading.Thread(target=_call_safe, args=(cb,), daemon=True).start()
                    elif wp in (0x101, 0x105):        # key up
                        down.discard(vk)
            except Exception:
                pass
            return user32.CallNextHookEx(None, n, wp, lp)

        self._proc = HOOKPROC(proc)
        return user32.SetWindowsHookExW(13, self._proc, kernel32.GetModuleHandleW(None), 0)  # WH_KEYBOARD_LL

    def run(self):
        if not IS_WIN:
            self.ready.set()
            return
        self.tid = kernel32.GetCurrentThreadId()
        cbs, fallback = {}, []
        for i, (mods, vk, cb) in enumerate(self.bindings, start=1):
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                cbs[i] = cb
            else:
                self.failed.append((mods, vk))
                fallback.append((mods, vk, cb))
        hook = None
        if fallback:
            try:
                hook = self._install_hook(fallback)
            except Exception as e:
                print("keyboard hook failed:", e)
            if hook:
                self.hooked = [(m, v) for m, v, _ in fallback]
        self.ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in cbs:
                _call_safe(cbs[msg.wParam])
        for i in cbs:
            user32.UnregisterHotKey(None, i)
        if hook:
            user32.UnhookWindowsHookEx(hook)

    def stop(self):
        if IS_WIN and self.tid:
            user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)


# --------------------------------------------------------------------------- windows / IME
def disable_ime():
    """Turn the input method off for HeadField's own windows (this process only;
    the game and other programs are not affected). Without this a Chinese IME
    swallows single-letter keys like o / c / r in the HeadField window.
    Must run before the first window is created."""
    if not IS_WIN:
        return False
    try:
        imm = ctypes.windll.imm32
        imm.ImmDisableIME.argtypes = [wintypes.DWORD]
        return bool(imm.ImmDisableIME(0xFFFFFFFF))   # -1 = all threads of this process
    except Exception:
        return False


def _find_window(title):
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    return user32.FindWindowW(None, title)


def no_ime_window(title):
    """Detach the input method from one window (backup for disable_ime)."""
    if not IS_WIN:
        return False
    try:
        hwnd = _find_window(title)
        if not hwnd:
            return False
        imm = ctypes.windll.imm32
        imm.ImmAssociateContextEx.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.DWORD]
        return bool(imm.ImmAssociateContextEx(hwnd, None, 0))
    except Exception:
        return False


def set_topmost(title, on=True):
    """Keep a window above others (does not take focus from the game).
    Has no effect over an exclusive-fullscreen game."""
    if not IS_WIN:
        return False
    hwnd = _find_window(title)
    if not hwnd:
        return False
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    after = ctypes.c_void_p(-1 if on else -2)   # HWND_TOPMOST / HWND_NOTOPMOST
    return bool(user32.SetWindowPos(hwnd, after, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010))  # NOSIZE|NOMOVE|NOACTIVATE


def beep(freq=1000, ms=60):
    if not IS_WIN:
        return
    import winsound
    threading.Thread(target=winsound.Beep, args=(freq, ms), daemon=True).start()


def beep_n(freq=1000, n=1, ms=60, gap=80):
    if not IS_WIN:
        return
    import winsound

    def run():
        for _ in range(n):
            winsound.Beep(freq, ms)
            time.sleep(gap / 1000)
    threading.Thread(target=run, daemon=True).start()


def foreground_exe():
    """Executable name of the foreground window's process (for the session log)."""
    if not IS_WIN:
        return ""
    import os
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    kernel32.OpenProcess.restype = wintypes.HANDLE
    h = kernel32.OpenProcess(0x1000, False, pid.value)
    if not h:
        return f"pid {pid.value}"
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value)
        return f"pid {pid.value}"
    finally:
        kernel32.CloseHandle(h)
