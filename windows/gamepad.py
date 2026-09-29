"""MacroPad Game — convierte las teclas del pad (rebindeadas a F13..F24)
en un gamepad Xbox 360 virtual (ViGEm bus + XInput).

Escucha F13..F24 con un hook global de teclado (WH_KEYBOARD_LL) y traduce
cada tecla a un botón del controlador virtual. Solo funciona mientras la
app está abierta.
"""

import ctypes
import queue
import threading
from ctypes import wintypes as w
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk

try:
    import vgamepad as vg
    HAVE_VG = True
except Exception:
    HAVE_VG = False

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", w.DWORD), ("scanCode", w.DWORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ctypes.c_ulonglong)]


HOOKPROC = ctypes.WINFUNCTYPE(w.LPARAM, ctypes.c_int, w.WPARAM, w.LPARAM)


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [("wButtons", w.WORD), ("bLeftTrigger", w.BYTE), ("bRightTrigger", w.BYTE),
                ("sThumbLX", w.SHORT), ("sThumbLY", w.SHORT),
                ("sThumbRX", w.SHORT), ("sThumbRY", w.SHORT)]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", w.DWORD), ("Gamepad", XINPUT_GAMEPAD)]


xinput = ctypes.WinDLL("xinput1_4.dll")
xinput.XInputGetState.argtypes = [w.DWORD, ctypes.POINTER(XINPUT_STATE)]
xinput.XInputGetState.restype = w.DWORD

F_KEYS = [(0x7C + i, f"F{13 + i}") for i in range(12)]  # VK_F13..VK_F24

BUTTONS = [
    ("A", "XUSB_GAMEPAD_A", 0x1000),
    ("B", "XUSB_GAMEPAD_B", 0x2000),
    ("X", "XUSB_GAMEPAD_X", 0x4000),
    ("Y", "XUSB_GAMEPAD_Y", 0x8000),
    ("LB", "XUSB_GAMEPAD_LEFT_SHOULDER", 0x0004),
    ("RB", "XUSB_GAMEPAD_RIGHT_SHOULDER", 0x0008),
    ("Back", "XUSB_GAMEPAD_BACK", 0x0020),
    ("Start", "XUSB_GAMEPAD_START", 0x0010),
    ("L3", "XUSB_GAMEPAD_LEFT_THUMB", 0x0040),
    ("R3", "XUSB_GAMEPAD_RIGHT_THUMB", 0x0080),
    ("Dpad ↑", "XUSB_GAMEPAD_DPAD_UP", 0x0001),
    ("Dpad ↓", "XUSB_GAMEPAD_DPAD_DOWN", 0x0002),
]


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("MacroPad Game — 514c:8850 → Xbox 360 virtual")
        self.geometry("560x560")
        self.resizable(False, False)
        self.hook = None
        self.proc = None
        self.running = False
        self.queue = queue.Queue()
        self.mapping = {}
        self.gamepad = None
        self._build_ui()
        self.after(100, self._drain)
        self.after(80, self._poll_xinput)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if not HAVE_VG:
            self._log("vgamepad no disponible — instalalo con: pip install vgamepad")
            return
        try:
            self.gamepad = vg.VX360Gamepad()
            self._log("gamepad Xbox 360 virtual conectado (ViGEm bus ✓)")
        except Exception as e:
            self._log(f"No pude crear el gamepad: {e}")
            return
        self._build_mapping()
        self.start()

    # ------------------------------------------------------------- UI
    def _build_ui(self):
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        self.lbl_status = ttk.Label(f, text="", font=("Segoe UI", 10))
        self.lbl_status.pack(anchor="w", pady=(0, 6))

        mf = ttk.LabelFrame(f, text="Mapeo: tecla del pad → botón Xbox")
        mf.pack(fill="x")
        grid = ttk.Frame(mf)
        grid.pack(padx=8, pady=8)
        self.combos = {}
        for i, (vk, name) in enumerate(F_KEYS):
            ttk.Label(grid, text=name, width=5).grid(row=i, column=0, sticky="e", pady=1)
            cb = ttk.Combobox(grid, values=[b[0] for b in BUTTONS], state="readonly", width=14)
            cb.current(i)
            cb.grid(row=i, column=1, padx=8, pady=1)
            cb.bind("<<ComboboxSelected>>", lambda e: self._build_mapping())
            self.combos[vk] = cb

        self.lbl_x = ttk.Label(f, text="XInput: —", font=("Consolas", 10))
        self.lbl_x.pack(anchor="w", pady=(8, 2))

        lf = ttk.LabelFrame(f, text="Log")
        lf.pack(fill="both", expand=True, pady=(6, 0))
        self.log = tk.Text(lf, height=8, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    def _log(self, text):
        self.queue.put(text)

    def _drain(self):
        try:
            while True:
                item = self.queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "event":
                    text = item[1]
                else:
                    text = item
                self.log.configure(state="normal")
                self.log.insert("end", text + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain)

    # ------------------------------------------------------------- mapping
    def _build_mapping(self):
        self.mapping = {}
        for vk, _ in F_KEYS:
            name = self.combos[vk].get()
            spec = next(b for b in BUTTONS if b[0] == name)
            self.mapping[vk] = SimpleNamespace(
                name=name, vg=getattr(vg.XUSB_BUTTON, spec[1]), xbit=spec[2])

    # ------------------------------------------------------------- hook
    def _hook_proc(self, nCode, wParam, lParam):
        if nCode >= 0:
            kbd = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = kbd.vkCode
            if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                self._press(vk, True)
            elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                self._press(vk, False)
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def start(self):
        if self.running:
            return
        self.proc = HOOKPROC(self._hook_proc)
        self.hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self.proc, None, 0)
        if not self.hook:
            self._log(f"hook falló ({ctypes.get_last_error()})")
            return
        self.running = True
        self.lbl_status.configure(text="✔ Escuchando F13..F24 — apretá las teclas del pad")
        self._log("hook activo: F13..F24 → botones Xbox")
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        msg = w.MSG()
        while self.running:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _press(self, vk, down):
        spec = self.mapping.get(vk)
        if not spec or not self.gamepad:
            return
        try:
            if down:
                self.gamepad.press_button(spec.vg)
            else:
                self.gamepad.release_button(spec.vg)
            self.gamepad.update()
        except Exception as e:
            self.queue.put(f"error gamepad: {e}")
            return
        self.queue.put(("event", f"  {spec.name} {'▼ presionado' if down else '▲ soltado'}"))

    # ------------------------------------------------------------- xinput
    def _poll_xinput(self):
        try:
            state = XINPUT_STATE()
            if xinput.XInputGetState(0, ctypes.byref(state)) == 0:
                bits = state.Gamepad.wButtons
                active = [name for (name, _, bit) in BUTTONS if bits & bit]
                self.lbl_x.configure(text="XInput: " + (", ".join(active) if active else "—"))
        except Exception:
            pass
        self.after(80, self._poll_xinput)

    def _on_close(self):
        self.running = False
        if self.hook:
            user32.UnhookWindowsHookEx(self.hook)
            self.hook = None
        if self.gamepad:
            try:
                self.gamepad.reset()
                self.gamepad.update()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()