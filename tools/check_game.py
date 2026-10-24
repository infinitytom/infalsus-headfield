"""Report whether the game (and Steam) run elevated. Numbers/text only."""
import ctypes, json, os
from ctypes import wintypes
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
k32, adv = ctypes.windll.kernel32, ctypes.windll.advapi32


class PE(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.OpenProcess.restype = wintypes.HANDLE


def elevated(pid):
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return "cannot open process"
    tok = wintypes.HANDLE()
    try:
        if not adv.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):
            return "token access denied (probably elevated)"
        e, r = wintypes.DWORD(), wintypes.DWORD()
        adv.GetTokenInformation(tok, 20, ctypes.byref(e), 4, ctypes.byref(r))
        k32.CloseHandle(tok)
        return bool(e.value)
    finally:
        k32.CloseHandle(h)


snap = k32.CreateToolhelp32Snapshot(0x2, 0)
pe = PE()
pe.dwSize = ctypes.sizeof(PE)
out = {"self_admin": bool(ctypes.windll.shell32.IsUserAnAdmin()), "processes": []}
ok = k32.Process32FirstW(snap, ctypes.byref(pe))
while ok:
    name = pe.szExeFile.lower()
    if name in ("infalsus.exe", "steam.exe"):
        out["processes"].append({"exe": pe.szExeFile, "pid": pe.th32ProcessID, "elevated": elevated(pe.th32ProcessID)})
    ok = k32.Process32NextW(snap, ctypes.byref(pe))
k32.CloseHandle(snap)
print(json.dumps(out, ensure_ascii=False, indent=2))
json.dump(out, open(os.path.join(ROOT, "logs", "check_game.json"), "w"), ensure_ascii=False, indent=2)
