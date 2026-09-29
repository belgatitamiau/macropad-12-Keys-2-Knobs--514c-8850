"""MacroPad 514c:8850 — Configurador (teclas + knobs + LEDs).

Standalone Windows app (tkinter). Uses only the HID stack (hid.dll / setupapi.dll)
via ctypes: no drivers, no USBDK.

Key/knob writes use the ch57x-3 (0xFD) format — the same one that works on this
unit via 'Alternative B' in the web tool. LED writes use the spokino format:
    preamble 03 FB FB FB, then 03 FE B0 <layer0> <mode> + 17 RGB triplets.

Never sends 0xEF (bootloader) or 0xFC (hardware variant).
"""

import ctypes
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from ctypes import wintypes as w

OUT_LEN = 65
IN_LEN = 65
TARGET_VID = 0x514C
TARGET_PID = 0x8850

# ----------------------------------------------------------------------------
# HID helpers (ctypes)
# ----------------------------------------------------------------------------

hid = ctypes.WinDLL("hid.dll")
setupapi = ctypes.WinDLL("setupapi.dll")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


GUID_DEVINTERFACE_HID = GUID(0x4D1E55B2, 0xF16F, 0x11CF,
                             (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11,
                                                   0x11, 0x00, 0x00, 0x30))


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", w.DWORD), ("Reserved", ctypes.c_void_p)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", w.ULONG), ("VendorID", w.USHORT),
                ("ProductID", w.USHORT), ("VersionNumber", w.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", w.USHORT), ("UsagePage", w.USHORT),
                ("InputReportByteLength", w.USHORT),
                ("OutputReportByteLength", w.USHORT),
                ("FeatureReportByteLength", w.USHORT),
                ("Reserved", w.USHORT * 17),
                ("NumberLinkCollectionNodes", w.USHORT),
                ("NumberInputButtonCaps", w.USHORT),
                ("NumberInputValueCaps", w.USHORT),
                ("NumberInputDataIndices", w.USHORT),
                ("NumberOutputButtonCaps", w.USHORT),
                ("NumberOutputValueCaps", w.USHORT),
                ("NumberOutputDataIndices", w.USHORT),
                ("NumberFeatureButtonCaps", w.USHORT),
                ("NumberFeatureValueCaps", w.USHORT),
                ("NumberFeatureDataIndices", w.USHORT)]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", w.DWORD), ("OffsetHigh", w.DWORD),
                ("hEvent", ctypes.c_void_p)]


setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), w.LPCWSTR, w.HWND, w.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                 ctypes.POINTER(GUID), w.DWORD,
                                                 ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p,
                                                      ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                      ctypes.c_void_p, w.DWORD,
                                                      ctypes.POINTER(w.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

hid.HidD_GetAttributes.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]

kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                 w.DWORD, w.DWORD, ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CreateEventW.restype = ctypes.c_void_p
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, w.DWORD]
kernel32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, w.DWORD,
                               ctypes.POINTER(w.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.GetOverlappedResult.argtypes = [ctypes.c_void_p, ctypes.POINTER(OVERLAPPED),
                                         ctypes.POINTER(w.DWORD), w.BOOL]
kernel32.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.POINTER(OVERLAPPED)]


def _hid_paths():
    paths = []
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(GUID_DEVINTERFACE_HID), None,
                                         None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not hdev or hdev == INVALID_HANDLE_VALUE:
        return paths
    try:
        idx = 0
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None,
                    ctypes.byref(GUID_DEVINTERFACE_HID), idx, ctypes.byref(did)):
                break
            req = w.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did),
                                                      None, 0, ctypes.byref(req), None)
            buf = ctypes.create_string_buffer(req.value)
            cb = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            ctypes.memmove(buf, ctypes.byref(w.DWORD(cb)), 4)
            if setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did),
                    buf, req.value, ctypes.byref(req), None):
                paths.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
            idx += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return paths


def _open(path):
    return kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                                FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)


def _caps(handle):
    attrs = HIDD_ATTRIBUTES()
    attrs.Size = ctypes.sizeof(attrs)
    if not hid.HidD_GetAttributes(handle, ctypes.byref(attrs)):
        return None
    preparsed = ctypes.c_void_p()
    caps = None
    if hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)) and preparsed:
        caps = HIDP_CAPS()
        if hid.HidP_GetCaps(preparsed, ctypes.byref(caps)) != 0x00110000:
            caps = None
        hid.HidD_FreePreparsedData(preparsed)
    return (attrs, caps)


def find_target():
    for path in _hid_paths():
        h = _open(path)
        if not h or h == INVALID_HANDLE_VALUE:
            continue
        try:
            got = _caps(h)
            if got:
                attrs, caps = got
                if (attrs.VendorID == TARGET_VID and attrs.ProductID == TARGET_PID
                        and caps and caps.UsagePage == 0xFF00 and caps.Usage == 0x0001):
                    return path, caps
        finally:
            kernel32.CloseHandle(h)
    return None, None


def sync_transfer(handle, is_write, payload, timeout_ms=3000):
    ev = kernel32.CreateEventW(None, True, False, None)
    ov = OVERLAPPED()
    ov.hEvent = ev
    buf = ctypes.create_string_buffer(bytes(payload), len(payload))
    done = w.DWORD(0)
    fn = kernel32.WriteFile if is_write else kernel32.ReadFile
    ok = fn(handle, buf, len(payload), ctypes.byref(done), ctypes.byref(ov))
    err = ctypes.get_last_error() if not ok else 0
    if not ok and err != 997:
        kernel32.CloseHandle(ev)
        return b"", err
    if not ok:
        rc = kernel32.WaitForSingleObject(ev, timeout_ms)
        if rc != 0:
            kernel32.CancelIoEx(handle, ctypes.byref(ov))
            kernel32.CloseHandle(ev)
            return b"", "timeout"
    got = w.DWORD(0)
    if not kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(got), True):
        kernel32.CloseHandle(ev)
        return b"", ctypes.get_last_error()
    kernel32.CloseHandle(ev)
    return buf.raw[:got.value], 0


# ----------------------------------------------------------------------------
# Protocol helpers (ch57x-3 / 0xFD for keys; spokino for LEDs)
# ----------------------------------------------------------------------------

KEYCODES = {
    "a": 0x04, "b": 0x05, "c": 0x06, "d": 0x07, "e": 0x08, "f": 0x09,
    "g": 0x0A, "h": 0x0B, "i": 0x0C, "j": 0x0D, "k": 0x0E, "l": 0x0F,
    "m": 0x10, "n": 0x11, "o": 0x12, "p": 0x13, "q": 0x14, "r": 0x15,
    "s": 0x16, "t": 0x17, "u": 0x18, "v": 0x19, "w": 0x1A, "x": 0x1B,
    "y": 0x1C, "z": 0x1D, "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21,
    "5": 0x22, "6": 0x23, "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27,
    "enter": 0x28, "esc": 0x29, "backspace": 0x2A, "tab": 0x2B,
    "space": 0x2C, "minus": 0x2D, "equal": 0x2E, "f1": 0x3A, "f2": 0x3B,
    "f3": 0x3C, "f4": 0x3D, "f5": 0x3E, "f6": 0x3F, "f7": 0x40,
    "f8": 0x41, "f9": 0x42, "f10": 0x43, "f11": 0x44, "f12": 0x45,
    "f13": 0x68, "f14": 0x69, "f15": 0x6A, "f16": 0x6B, "f17": 0x6C,
    "f18": 0x6D, "f19": 0x6E, "f20": 0x6F, "f21": 0x70, "f22": 0x71,
    "f23": 0x72, "f24": 0x73,
    "insert": 0x49, "home": 0x4A, "pageup": 0x4B, "delete": 0x4C,
    "end": 0x4D, "pagedown": 0x4E, "right": 0x4F, "left": 0x50,
    "down": 0x51, "up": 0x52,
}
MODS = {"ctrl": 0xF1, "shift": 0xF2, "alt": 0xF3, "win": 0xF4,
        "rctrl": 0xF5, "rshift": 0xF6, "ralt": 0xF7, "rwin": 0xF8}
MEDIA = {"volumeup": 0xE9, "volumedown": 0xEA, "mute": 0xE2,
         "play": 0xCD, "next": 0xB5, "prev": 0xB6, "stop": 0xB7}
EMPTY_WORDS = {"", "ninguno", "none", "empty", "borrar", "del", "nada"}

SLOT_BY_POS = [4, 8, 12, 3, 7, 11, 2, 6, 10, 1, 5, 9]
# Mismo layout rotado 90° a la izquierda (knobs izq): display 4x3
SLOT_BY_POS_ROT = [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
KNOB_BASE = 16  # VERIFICADO en hardware: knob1 = 16..18 (ccw,press,cw), knob2 = 19..21
KNOB_ACTIONS = ["ccw", "press", "cw"]
KNOB_GLYPH = {"ccw": "◀", "press": "▼", "cw": "▶"}

REV_MODS = {0xF1: "ctrl", 0xF2: "shift", 0xF3: "alt", 0xF4: "win",
            0xF5: "rctrl", 0xF6: "rshift", 0xF7: "ralt", 0xF8: "rwin"}
REV_KEY = {v: k for k, v in KEYCODES.items()}


def decode_record_macro(raw):
    """Convierte un registro 0xFA de tecla a texto de macro (o None si está vacío)."""
    if len(raw) < 10 or raw[1] != 0xFA or raw[4] != 1 or raw[5] != 0:
        return None
    count = raw[6]
    if count > 18:
        count = 18
    codes = []
    for i in range(count):
        idx = 9 + 3 * i
        if idx >= len(raw):
            break
        codes.append(raw[idx])
    segs = []
    mods = []
    for c in codes:
        if c == 0:
            continue
        if 0xF1 <= c <= 0xF8:
            mods.append(REV_MODS[c])
            continue
        key = REV_KEY.get(c, f"<{c}>")
        segs.append("-".join(mods + [key]) if mods else key)
        mods = []
    if mods:
        segs.append("-".join(mods))
    return ",".join(segs) if segs else None


def parse_binding(s):
    """-> ("empty"|"media"|"keys", data) or raises ValueError."""
    s = (s or "").strip().lower()
    if s in EMPTY_WORDS:
        return ("empty", None)
    if s in MEDIA:
        return ("media", MEDIA[s])
    if s in ("click", "rclick", "mclick", "wheelup", "wheeldown", "move"):
        raise ValueError("mouse aún no está en el exe — usá el web tool (Alternative B)")
    entries = []
    for part in s.split(","):
        toks = [t.strip() for t in part.split("-") if t.strip()]
        if not toks:
            raise ValueError(f"paso vacío en '{part}'")
        mods = []
        while toks and toks[0] in MODS:
            mods.append(toks.pop(0))
        if not toks:
            if mods:
                entries.extend(MODS[m] for m in mods)
                continue
            raise ValueError(f"paso vacío en '{part}'")
        key = toks[0]
        if len(toks) > 1:
            raise ValueError(f"no entendí '{part}' — separá con comas: a,b,c")
        if key not in KEYCODES:
            raise ValueError(f"tecla desconocida: '{key}'")
        for m in mods:
            entries.append(MODS[m])
        entries.append(KEYCODES[key])
    if not entries:
        raise ValueError("macro vacía")
    return ("keys", entries)


def build_record(slot, layer1, parsed):
    kind, data = parsed
    if kind == "empty":
        return [0x03, 0xFD, slot, layer1, 0x01, 0x00, 0x01, 0x00, 0x00, 0x00]
    if kind == "media":
        return [0x03, 0xFD, slot, layer1, 0x02, 0x00, 0x01, 0x00, 0x00, data]
    count = len(data)
    rec = [0x03, 0xFD, slot, layer1, 0x01, 0x00, count]
    for c in data:
        rec += [0x00, 0x00, c]
    return rec


class Pad:

    def __init__(self):
        self.h = None
        self.path = None

    def connect(self):
        self.close()
        path, caps = find_target()
        if not path:
            return "No se encontró el pad 514c:8850"
        h = _open(path)
        if not h or h == INVALID_HANDLE_VALUE:
            return "No se pudo abrir el device (%d)" % ctypes.get_last_error()
        self.h, self.path = h, path
        return None

    def close(self):
        if self.h:
            kernel32.CloseHandle(self.h)
            self.h = None
            self.path = None

    def send(self, payload):
        msg = bytes(payload) + b"\x00" * (OUT_LEN - len(payload))
        _, err = sync_transfer(self.h, True, msg)
        time.sleep(0.03)
        return err

    def write_binding(self, slot, layer1, text):
        parsed = parse_binding(text)
        rec = build_record(slot, layer1, parsed)
        if self.send(rec):
            raise RuntimeError("write falló")
        if self.send([0x03, 0xFD, 0xFE, 0xFF]):
            raise RuntimeError("terminador falló")

    def led_write(self, layer0, mode, rgb):
        if self.send([0x03, 0xFB, 0xFB, 0xFB]):
            raise RuntimeError("preamble write failed")
        pkt = [0x03, 0xFE, 0xB0, layer0 & 0xFF, mode & 0xFF]
        for _ in range(17):
            pkt += [rgb[0] & 0xFF, rgb[1] & 0xFF, rgb[2] & 0xFF]
        if self.send(pkt):
            raise RuntimeError("LED record write failed")

    def identity(self):
        if self.send([0x03, 0xFB, 0xFB, 0xFB]):
            return None, None
        for _ in range(4):
            data, err = sync_transfer(self.h, False, bytes(IN_LEN), 1200)
            if err:
                break
            if data and data[1] == 0xFB:
                return data[2], data[3]
        return None, None

    def read_layer(self, layer, keys=12, knobs=2):
        """Lee los registros 0xFA de una capa (teclas). Devuelve lista de 65-byte."""
        msg = bytes([0x03, 0xFA, keys, knobs, layer]) + b"\x00" * (OUT_LEN - 5)
        self.send(msg)
        recs = []
        empties = 0
        for _ in range(80):
            data, err = sync_transfer(self.h, False, bytes(IN_LEN), 600)
            if err or not data:
                empties += 1
                if empties >= 3:
                    break
                continue
            if len(data) >= 3 and data[1] == 0xFA:
                empties = 0
                recs.append(data)
            else:
                break
        return recs


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------

MODES = [("Apagado", 0), ("Fijo", 1), ("Reacciona", 2), ("Ripple", 3),
         ("Arcoíris", 4), ("Arcoíris 2", 5)]
PRESETS = [("Rojo", (255, 0, 0)), ("Naranja", (255, 128, 0)),
           ("Amarillo", (255, 255, 0)), ("Verde", (0, 255, 0)),
           ("Cian", (0, 255, 255)), ("Azul", (0, 0, 255)),
           ("Violeta", (128, 0, 255)), ("Blanco", (255, 255, 255))]


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("MacroPad 514c:8850 — Teclas y LEDs")
        self.geometry("640x680")
        self.minsize(600, 620)
        self.resizable(True, True)
        self._apply_dark()
        self.pad = Pad()
        self.logq = queue.Queue()
        self.profile = {1: {}, 2: {}, 3: {}}

        self.var_layer = tk.IntVar(value=1)
        self.var_mode = tk.IntVar(value=1)
        self.var_all = tk.BooleanVar(value=True)
        self.var_r = tk.IntVar(value=255)
        self.var_g = tk.IntVar(value=0)
        self.var_b = tk.IntVar(value=0)
        self.var_led_layer = tk.IntVar(value=0)
        self._led_timer = None
        self.var_quick = tk.StringVar()
        self.quick_mods = {}
        self._pk = None
        self._pn = None

        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        self.lbl_status = ttk.Label(f, text="buscando device...", font=("Segoe UI", 10))
        self.lbl_status.pack(anchor="w")
        ttk.Button(f, text="Conectar / Reconectar", command=self.connect_device).pack(anchor="w", pady=(4, 8))

        nb = ttk.Notebook(f)
        nb.pack(fill="both", expand=True)
        t_keys = ttk.Frame(nb)
        t_led = ttk.Frame(nb)
        nb.add(t_keys, text="Teclas")
        nb.add(t_led, text="LEDs")
        self._build_keys(t_keys)
        self._build_leds(t_led)

        lgf = ttk.LabelFrame(f, text="Log")
        lgf.pack(fill="both", expand=True, pady=(8, 0))
        self.log = tk.Text(lgf, height=7, state="disabled", font=("Consolas", 9),
                       bg=self.DARK_BG, fg=self.DARK_FG,
                       insertbackground=self.DARK_FG,
                       selectbackground=self.DARK_ACCENT,
                       highlightbackground=self.DARK_BG,
                       highlightcolor=self.DARK_BG,
                       relief="flat", bd=0)
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

        self.after(100, self._drain_logs)
        self.connect_device()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # DWM dark title: la ventana tiene que estar mapeada primero
        self.after(250, self._apply_dwm_dark)
        self.after(1200, self._apply_dwm_dark)

    DARK_BG = "#1c1e22"
    DARK_FG = "#e6e8ee"
    DARK_PANEL = "#262a31"
    DARK_PANEL2 = "#22242a"
    DARK_ACCENT = "#3d8bfd"
    DARK_MUTED = "#8fa0b8"

    def _apply_dark(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=self.DARK_BG, foreground=self.DARK_FG,
                     fieldbackground=self.DARK_BG, bordercolor=self.DARK_BG,
                     troughcolor=self.DARK_PANEL2, selectbackground=self.DARK_ACCENT)
        st.configure("TFrame", background=self.DARK_BG)
        st.configure("TLabel", background=self.DARK_BG, foreground=self.DARK_FG)
        st.configure("TLabelframe", background=self.DARK_BG, foreground=self.DARK_FG,
                     bordercolor="#3a3f4a")
        st.configure("TLabelframe.Label", background=self.DARK_BG, foreground=self.DARK_FG)
        st.configure("TButton", background=self.DARK_PANEL, foreground=self.DARK_FG,
                     bordercolor="#3a3f4a", padding=4)
        st.map("TButton", background=[("active", "#343942"), ("pressed", self.DARK_PANEL2)])
        st.configure("TRadiobutton", background=self.DARK_BG, foreground=self.DARK_FG)
        st.map("TRadiobutton", background=[("active", self.DARK_BG)])
        st.configure("TCheckbutton", background=self.DARK_BG, foreground=self.DARK_FG)
        st.map("TCheckbutton", background=[("active", self.DARK_BG)])
        st.configure("TNotebook", background=self.DARK_BG, bordercolor="#3a3f4a")
        st.configure("TNotebook.Tab", background=self.DARK_PANEL, foreground=self.DARK_FG,
                     padding=(12, 5))
        st.map("TNotebook.Tab", background=[("selected", "#343942")])
        st.configure("TCombobox", fieldbackground=self.DARK_BG, background=self.DARK_BG,
                     foreground=self.DARK_FG, arrowcolor=self.DARK_FG,
                     darkcolor=self.DARK_BG, lightcolor=self.DARK_BG)
        st.configure("TEntry", fieldbackground="#2e333b", foreground="#ffffff",
                     bordercolor="#3a3f4a", lightcolor="#3a3f4a", darkcolor="#3a3f4a",
                     insertcolor="#ffffff")
        st.configure("Horizontal.TScale", background=self.DARK_BG, troughcolor=self.DARK_PANEL2)
        self.configure(bg=self.DARK_BG)
        self._apply_dwm_dark()

    def _apply_dwm_dark(self, widget=None):
        """Titulo y bordes de la ventana oscuros (DWM)."""
        try:
            hwnd = (widget or self).winfo_id()
            dwm = ctypes.WinDLL("dwmapi.dll")
            v = ctypes.c_int(1)
            if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), ctypes.sizeof(v)):
                dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(v), ctypes.sizeof(v))
            col = ctypes.c_int(0x221E1C)  # ABGR oscuro #1c1e22
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(col), ctypes.sizeof(col))
        except Exception:
            pass

    # ------------------------------------------------------------------ keys
    def _build_keys(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x", pady=(8, 2))
        ttk.Label(top, text="Capa:").pack(side="left")
        for i in (1, 2, 3):
            ttk.Radiobutton(top, text=str(i), value=i, variable=self.var_layer,
                            command=self._refresh_keys).pack(side="left", padx=3)
        ttk.Button(top, text="Escribir capa", command=lambda: self.write_layer(self.var_layer.get())).pack(side="left", padx=6)
        ttk.Button(top, text="Escribir todo", command=self.write_all).pack(side="left", padx=3)
        ttk.Button(top, text="Vaciar capa", command=self.blank_layer).pack(side="left", padx=3)

        hint = "Clic en una tecla/knob para asignar. Sintaxis: a, ctrl-shift-s, a,b,c · media: volumeup/play/mute · borrar"
        ttk.Label(parent, text=hint, wraplength=470, foreground=self.DARK_MUTED).pack(fill="x", pady=(0, 6))

        lf = ttk.LabelFrame(parent, text="Layout (rotado 90° — knobs a la izquierda)")
        lf.pack(fill="both", expand=True, padx=4, pady=(0, 6))
        mid = ttk.Frame(lf)
        mid.pack(fill="both", expand=True, padx=8, pady=6)

        kf = ttk.Frame(mid)
        kf.pack(side="left", padx=(0, 12), fill="y")
        kf.rowconfigure(0, weight=1)
        kf.rowconfigure(3, weight=1)
        self.knob_canvases = {}
        self.knob_texts = {}
        for row, k in zip((1, 2), (1, 0)):  # knob 2 arriba, knob 1 abajo
            cv = tk.Canvas(kf, width=96, height=86, bg=self.DARK_BG, highlightthickness=0)
            cv.grid(row=row, column=0, pady=2)
            cv.bind("<Button-1>", lambda e, kk=k: self._knob_click(kk, e.x, e.y))
            self.knob_canvases[k] = cv
            self._draw_knob(k, cv)

        gf = ttk.Frame(mid)
        gf.pack(side="left", fill="both", expand=True)
        gf.rowconfigure(0, weight=1)
        gf.rowconfigure(5, weight=1)
        self.key_buttons = {}
        for pos in range(12):
            r, c = divmod(pos, 4)  # grilla rotada: 4 columnas x 3 filas
            b = tk.Button(gf, text="", width=9, height=3, font=("Segoe UI", 8),
                          relief="flat", bd=1, bg=self.DARK_PANEL2,
                          fg=self.DARK_FG, activebackground="#343942",
                          activeforeground=self.DARK_FG,
                          highlightthickness=0, highlightbackground=self.DARK_BG,
                          highlightcolor=self.DARK_BG, takefocus=0)
            b.grid(row=1 + r, column=c, padx=3, pady=2)
            b.configure(command=lambda p=pos: self.edit_cell(SLOT_BY_POS_ROT[p],
                                                             f"Tecla {p + 1}"))
            self.key_buttons[pos] = b
        ttk.Label(gf, text="K1..K12 rotados — knobs a la izquierda").grid(
            row=4, column=0, columnspan=4, sticky="w", padx=4)

        qf = ttk.LabelFrame(parent, text="Asignación rápida")
        qf.pack(fill="x", padx=4, pady=(0, 6))
        qrow = ttk.Frame(qf)
        qrow.pack(fill="x", padx=8, pady=(6, 2))
        ent = ttk.Entry(qrow, textvariable=self.var_quick, width=36)
        ent.grid(row=0, column=0, columnspan=8, sticky="we", pady=(0, 4))
        qrow.columnconfigure(0, weight=1)
        ttk.Button(qrow, text="Limpiar", command=self._quick_clear).grid(
            row=0, column=8, padx=6, sticky="e")
        mod_names = [("ctrl", "ctrl"), ("shift", "shift"), ("alt", "alt"), ("win", "win"),
                     ("rctrl", "rctrl"), ("rshift", "rshift"), ("ralt", "ralt"), ("rwin", "rwin")]
        for i, (label, key) in enumerate(mod_names):
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(qrow, text=label, variable=var).grid(
                row=1, column=i, sticky="w", padx=2, pady=(0, 4))
            self.quick_mods[key] = var
        ttk.Label(qf, text="Escribí un macro (ej: a · ctrl-a · 1,2,3) y tocá una tecla o knob para asignarlo directo. "
                           "Con la barra vacía, el clic abre el editor.",
                  foreground=self.DARK_MUTED, wraplength=560).pack(anchor="w", padx=8, pady=(0, 6))

        self._refresh_keys()

    def _refresh_keys(self):
        layer = self.var_layer.get()
        for pos in range(12):
            slot = SLOT_BY_POS_ROT[pos]
            txt = self.profile[layer].get(slot)
            b = self.key_buttons[pos]
            b.configure(text=f"K{pos + 1}\n{(txt or '—')[:12]}")
        for k in range(2):
            texts = self.knob_texts.get(k)
            if not texts:
                continue
            for a, act in enumerate(KNOB_ACTIONS):
                slot = KNOB_BASE + 3 * k + a
                txt = self.profile[layer].get(slot)
                self.knob_canvases[k].itemconfigure(
                    texts[act], text=f"{KNOB_GLYPH[act]} {(txt or '—')[:6]}")

    def _draw_knob(self, k, cv):
        cv.delete("all")
        cx, cy = 48, 44
        r_out, r_in = 34, 20
        cv.create_oval(cx - r_out, cy - r_out, cx + r_out, cy + r_out,
                       fill="#3d434d", outline="#59616e", width=3)
        cv.create_oval(cx - r_in, cy - r_in, cx + r_in, cy + r_in,
                       fill="#24282e", outline="#3a4048", width=2)
        for i in range(16):
            a = math.pi * 2 * i / 16
            x1 = cx + math.sin(a) * r_in
            y1 = cy - math.cos(a) * r_in
            x2 = cx + math.sin(a) * (r_in + 6)
            y2 = cy - math.cos(a) * (r_in + 6)
            cv.create_line(x1, y1, x2, y2, fill="#59616e")
        cv.create_line(cx, cy, cx, cy - r_in + 3, fill="#ffd166",
                       width=4, capstyle="round")
        cv.create_oval(cx - 5, cy - 5, cx + 5, cy + 5,
                       fill="#0d0f12", outline="#6b7480")
        cv.create_text(cx, 10, text=f"Knob {k + 1}", fill="#8a93a0",
                       font=("Segoe UI", 8, "bold"))
        texts = {}
        xy = [(22, 78), (48, 78), (74, 78)]
        for a, act in enumerate(KNOB_ACTIONS):
            texts[act] = cv.create_text(xy[a][0], xy[a][1], text="",
                                        fill="#ffd166", font=("Segoe UI", 9, "bold"))
        self.knob_texts[k] = texts

    def _knob_click(self, k, x, y):
        cx, cy = 48, 44
        dx, dy = x - cx, y - cy
        r = math.hypot(dx, dy)
        if r > 46:
            return
        if r < 18:
            act = "press"
        elif dx < -8:
            act = "ccw"
        elif dx > 8:
            act = "cw"
        else:
            act = "press"
        slot = KNOB_BASE + 3 * k + KNOB_ACTIONS.index(act)
        self.edit_cell(slot, f"Knob {k + 1} {act}")

    def _quick_macro(self):
        mods = [m for m in ("ctrl", "shift", "alt", "win", "rctrl", "rshift", "ralt", "rwin")
                if self.quick_mods.get(m) and self.quick_mods[m].get()]
        text = self.var_quick.get().strip().lower()
        if not mods and not text:
            return None
        if text:
            return "-".join(mods + [text]) if mods else text
        return "-".join(mods)

    def _quick_clear(self):
        self.var_quick.set("")
        for v in self.quick_mods.values():
            v.set(False)

    def edit_cell(self, slot, label):
        layer = self.var_layer.get()
        quick = self._quick_macro()
        if quick is not None:
            self.profile[layer][slot] = quick
            self._refresh_keys()
            self.log_msg(f"{label} ← {quick} (barra rápida — seguí tocando para asignar igual)")
            return
        top = tk.Toplevel(self)
        top.configure(bg=self.DARK_BG)
        self._apply_dwm_dark(top)
        top.title(f"{label} — capa {layer}")
        top.transient(self)
        top.grab_set()
        ttk.Label(top, text="Macro (ej: 'ctrl-alt-shift-s', 'a,b,c', 'volumeup', 'borrar'):").pack(padx=10, pady=(10, 2))
        var = tk.StringVar(value=self.profile[layer].get(slot, ""))
        entry = ttk.Entry(top, textvariable=var, width=42)
        entry.pack(padx=10)
        err = ttk.Label(top, text="", foreground="#ff6b6b")
        err.pack()
        btns = ttk.Frame(top)
        btns.pack(pady=8)

        def ok():
            try:
                parse_binding(var.get())
            except ValueError as e:
                err.configure(text=str(e))
                return
            self.profile[layer][slot] = var.get().strip().lower()
            self._refresh_keys()
            top.destroy()

        def clear():
            self.profile[layer].pop(slot, None)
            self._refresh_keys()
            top.destroy()

        ttk.Button(btns, text="Guardar", command=ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Vaciar espacio", command=clear).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancelar", command=top.destroy).pack(side="left", padx=4)
        entry.focus_set()

    def _all_slots(self):
        slots = list(SLOT_BY_POS_ROT)
        for k in range(2):
            for a in range(3):
                slots.append(KNOB_BASE + 3 * k + a)
        return slots

    def write_layer(self, layer):
        if not self.pad.h:
            self.log_msg("No hay device conectado.")
            return
        n = 0
        for slot in self._all_slots():
            try:
                self.pad.write_binding(slot, layer,
                                       self.profile[layer].get(slot, "borrar"))
                n += 1
            except (RuntimeError, ValueError) as e:
                self.log_msg(f"ERROR slot {slot}: {e}")
                return
        self.log_msg(f"Capa {layer} escrita ({n} espacios)")

    def write_all(self):
        if not self.pad.h:
            self.log_msg("No hay device conectado.")
            return
        for layer in (1, 2, 3):
            self.write_layer(layer)

    def blank_layer(self):
        self.profile[self.var_layer.get()] = {}
        self._refresh_keys()
        self.write_layer(self.var_layer.get())

    # ------------------------------------------------------------------ leds
    def _build_leds(self, parent):
        ttk.Label(parent, text="Los cambios de modo / color / capa se aplican automáticamente",
                  foreground=self.DARK_MUTED).pack(anchor="w", pady=(6, 0))
        lf = ttk.LabelFrame(parent, text="Modo")
        lf.pack(fill="x", pady=(8, 0))
        row = ttk.Frame(lf)
        row.pack(fill="x", padx=8, pady=6)
        for i, (label, val) in enumerate(MODES):
            ttk.Radiobutton(row, text=label, value=val, variable=self.var_mode,
                            command=self.apply_led).grid(row=i // 8, column=i % 8,
                                                         sticky="w", padx=3, pady=2)

        layf = ttk.LabelFrame(parent, text="Capa (suele aplicar a todas igual)")
        layf.pack(fill="x", pady=(8, 0))
        row2 = ttk.Frame(layf)
        row2.pack(fill="x", padx=8, pady=6)
        for label in ("1", "2", "3"):
            ttk.Radiobutton(row2, text=f"Capa {label}", value=int(label) - 1,
                            variable=self.var_led_layer,
                            command=self.apply_led).pack(side="left", padx=4)
        ttk.Checkbutton(row2, text="Todas las capas", variable=self.var_all,
                        command=self.apply_led).pack(side="left", padx=12)

        cf = ttk.LabelFrame(parent, text="Color (aplica a las 12 teclas)")
        cf.pack(fill="x", pady=(8, 0))
        grid = ttk.Frame(cf)
        grid.pack(fill="x", padx=8, pady=6)
        self.swatch = tk.Canvas(grid, width=28, height=28, highlightthickness=0,
                            bg=self.DARK_PANEL2)
        self.swatch.grid(row=0, column=0, rowspan=3, padx=(0, 10))
        self._paint_swatch()
        for i, (name, var) in enumerate((("R", self.var_r), ("G", self.var_g), ("B", self.var_b))):
            ttk.Label(grid, text=name).grid(row=i, column=1, sticky="e")
            s = ttk.Scale(grid, from_=0, to=255, variable=var,
                          command=lambda _x: (self._paint_swatch(),
                                              self._schedule_apply()), length=180)
            s.grid(row=i, column=2, padx=6)
            ttk.Label(grid, textvariable=var, width=3).grid(row=i, column=3)
        pres = ttk.Frame(cf)
        pres.pack(fill="x", padx=8, pady=(0, 8))
        for name, rgb in PRESETS:
            ttk.Button(pres, text=name, width=7,
                       command=lambda c=rgb: (self._set_preset(c),
                                              self.apply_led())).pack(side="left", padx=2)

        af = ttk.Frame(parent)
        af.pack(fill="x", pady=10)
        ttk.Button(af, text="Aplicar LEDs", command=self.apply_led).pack(side="left", padx=(0, 6))
        ttk.Button(af, text="Apagar todo", command=self.off_led).pack(side="left", padx=6)
        ttk.Button(af, text="Test rápido", command=self.test_led).pack(side="left", padx=6)

    def _paint_swatch(self, rgb=None):
        rgb = rgb or (self.var_r.get(), self.var_g.get(), self.var_b.get())
        self.swatch.configure(bg="#%02x%02x%02x" % rgb)

    def _set_preset(self, rgb):
        self.var_r.set(rgb[0])
        self.var_g.set(rgb[1])
        self.var_b.set(rgb[2])
        self._paint_swatch(rgb)

    def _rgb(self):
        return (self.var_r.get(), self.var_g.get(), self.var_b.get())

    def _led_layers(self):
        if self.var_all.get():
            return [0, 1, 2]
        return [self.var_led_layer.get()]

    def apply_led(self, verbose=True):
        if not self.pad.h:
            self.log_msg("No hay device conectado.")
            return
        mode = self.var_mode.get()
        rgb = self._rgb()
        try:
            for layer0 in self._led_layers():
                self.pad.led_write(layer0, mode, rgb)
        except RuntimeError as e:
            self.log_msg(f"ERROR: {e}")
            return
        if verbose:
            self.log_msg(f"LED: modo={mode} rgb={rgb}")

    def _schedule_apply(self):
        if self._led_timer is not None:
            self.after_cancel(self._led_timer)
        self._led_timer = self.after(150, self._auto_apply)

    def _auto_apply(self):
        self._led_timer = None
        self.apply_led(verbose=False)

    def off_led(self):
        if not self.pad.h:
            self.log_msg("No hay device conectado.")
            return
        for layer0 in [0, 1, 2]:
            try:
                self.pad.led_write(layer0, 0, (0, 0, 0))
            except RuntimeError as e:
                self.log_msg(f"ERROR: {e}")
                return
        self.log_msg("LEDs apagados")

    def test_led(self):
        if not self.pad.h:
            self.log_msg("No hay device conectado.")
            return
        self.log_msg("Test: ciclo de colores...")
        threading.Thread(target=self._test_run, daemon=True).start()

    def _test_run(self):
        try:
            for rgb in [p[1] for p in PRESETS] + [(255, 255, 255)]:
                for layer0 in [0, 1, 2]:
                    self.pad.led_write(layer0, 1, rgb)
                time.sleep(0.25)
            for layer0 in [0, 1, 2]:
                self.pad.led_write(layer0, 1, self._rgb())
            self.log_msg("Test terminado (volvió al color elegido)")
        except RuntimeError as e:
            self.log_msg(f"ERROR en test: {e}")

    # ------------------------------------------------------------------ misc
    def log_msg(self, text):
        self.logq.put(text)

    def _drain_logs(self):
        try:
            while True:
                msg = self.logq.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain_logs)

    def connect_device(self):
        self.log_msg("Conectando...")
        err = self.pad.connect()
        if err:
            self.lbl_status.configure(text=f"⚠ {err}")
            self.log_msg(f"ERROR: {err}")
            return
        keys, knobs = self.pad.identity()
        self.lbl_status.configure(text=f"✔ Conectado: 514c:8850 · {keys} teclas, {knobs} knobs")
        self.log_msg(f"Conectado: {self.pad.path}")
        self._pk, self._pn = keys, knobs
        self._read_pad_profile()

    def _read_pad_profile(self):
        profile = {1: {}, 2: {}, 3: {}}
        if not (self.pad.h and self._pk and self._pn):
            return
        for layer in (1, 2, 3):
            try:
                recs = self.pad.read_layer(layer, self._pk, self._pn)
            except Exception:
                continue
            for raw in recs:
                slot = raw[2] if len(raw) >= 3 else None
                if slot is None:
                    continue
                text = decode_record_macro(raw)
                if text:
                    profile[layer][slot] = text
        self.profile = profile
        self._refresh_keys()
        self.log_msg("Perfil cargado desde el pad (teclas). Los knobs no son legibles por el firmware.")

    def _on_close(self):
        self.pad.close()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()