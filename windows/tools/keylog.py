"""Key logger — qué emite el pad realmente (vk + scan) por 14 s."""

import ctypes
import time
from ctypes import wintypes as w

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", w.DWORD), ("scanCode", w.DWORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ctypes.c_ulonglong)]


HOOKPROC = ctypes.WINFUNCTYPE(w.LPARAM, ctypes.c_int, w.WPARAM, w.LPARAM)

events = []
deadline = time.time() + 14


def proc(nCode, wParam, lParam):
    if nCode >= 0 and time.time() < deadline:
        kbd = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        kind = {WM_KEYDOWN: "DOWN", WM_SYSKEYDOWN: "DOWNs", WM_KEYUP: "UP", WM_SYSKEYUP: "UPs"}.get(wParam, "?")
        if kbd.vkCode != 0:
            print(f"[{time.time():.1f}] {kind} vk=0x{kbd.vkCode:02x} scan=0x{kbd.scanCode:02x} flags=0x{kbd.flags:02x}", flush=True)
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


_hookproc = HOOKPROC(proc)
hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _hookproc, None, 0)
if not hook:
    print("hook falló", ctypes.get_last_error())
    raise SystemExit(1)

print("APRETÁ SOLO EL PAD: K1, K2, K3, K4 y el knob 1 (presionar + girar). 14 segundos...", flush=True)
msg = w.MSG()
while time.time() < deadline:
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    time.sleep(0.01)
user32.UnhookWindowsHookEx(hook)
print("listo.")