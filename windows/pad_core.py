"""MacroPad 514c:8850 — core de protocolo para el backend de Windows.

Transporte HID (hid.dll / setupapi.dll) tomado de windows/macropad_configurator.py
y helpers de protocolo verificados del core de Linux: ch57x-3 (0xFD) para teclas
y knobs, spokino (0xFE B0) para LEDs, lectura 0xFA con mouse/media/modificadores.

Sin tkinter, sin drivers extra, sin 0xEF (bootloader) ni 0xFC.
"""

import ctypes
import errno
import os
import time
from ctypes import wintypes as w

_APPDATA = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
_CONF_DIR = os.path.join(_APPDATA, "macropad-configurador")
LOCAL_PROFILE = os.path.join(_CONF_DIR, "perfil-514c-8850.json")

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
MODS = {"ctrl": 0xF1, "shift": 0xF2, "alt": 0xF3, "win": 0xF4,        "rctrl": 0xF5, "rshift": 0xF6, "ralt": 0xF7, "rwin": 0xF8}
MEDIA = {"volumeup": 0xE9, "volumedown": 0xEA, "mute": 0xE2,
         "play": 0xCD, "next": 0xB5, "prev": 0xB6, "stop": 0xB7}
EMPTY_WORDS = {"", "ninguno", "none", "empty", "borrar", "del", "nada"}

# Mouse (dialecto ch57x-3): acción -> (tipo, valor). click = botón izq, etc.
MOUSE_ACTIONS = {"click": ("click", 1), "rclick": ("click", 2),
                 "mclick": ("click", 4),
                 "wheelup": ("wheel", 1), "wheeldown": ("wheel", -1)}
MOUSE_MODS = {"ctrl": 0xF1, "shift": 0xF2, "alt": 0xF3}

SLOT_BY_POS = [4, 8, 12, 3, 7, 11, 2, 6, 10, 1, 5, 9]
# Mismo layout rotado 90° a la izquierda (knobs izq): display 4x3
SLOT_BY_POS_ROT = [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
KNOB_BASE = 16  # VERIFICADO en hardware: knob1 = 16..18 (ccw,press,cw), knob2 = 19..21
KNOB_ACTIONS = ["ccw", "press", "cw"]
KNOB_GLYPH = {"ccw": "◀", "press": "▼", "cw": "▶"}

REV_MODS = {0xF1: "ctrl", 0xF2: "shift", 0xF3: "alt", 0xF4: "win",
            0xF5: "rctrl", 0xF6: "rshift", 0xF7: "ralt", 0xF8: "rwin"}
REV_KEY = {v: k for k, v in KEYCODES.items()}
REV_MEDIA = {v: k for k, v in MEDIA.items()}


def decode_record_macro(raw):
    """Convierte un registro 0xFA de tecla a texto de macro (o None si está vacío).

    Layout verificado contra el pad real (lectura 0xFA):
      raw[2]=slot, raw[3]=capa, raw[4]=tipo (1=teclas, 2=media, 3=mouse)
      teclas : raw[6]=cantidad, códigos en 9+3*i
      media  : raw[9]=código
      mouse  : md = raw[5:], md[4]=mod, md[7]=botón, md[16]=rueda
    """
    if len(raw) < 10 or raw[1] != 0xFA:
        return None
    kind = raw[4]

    if kind == 2:
        return REV_MEDIA.get(raw[9])

    if kind == 3:
        md = raw[5:]
        if len(md) < 17:
            return None
        mods = [REV_MODS[md[4]]] if md[4] in REV_MODS else []
        wheel = md[16]
        if wheel:
            act = "wheelup" if wheel < 128 else "wheeldown"
        else:
            act = {1: "click", 2: "rclick", 4: "mclick"}.get(md[7])
        if not act:
            return None
        return "-".join(mods + [act])

    if kind != 1:
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
    if not segs:
        return None
    return ",".join(segs)


def parse_binding(s):
    """-> ("empty"|"media"|"keys"|"mouse", data) or raises ValueError."""
    s = (s or "").strip().lower()
    if s in EMPTY_WORDS:
        return ("empty", None)
    if s in MEDIA:
        return ("media", MEDIA[s])
    toks0 = [t.strip() for t in s.split("-") if t.strip()]
    if toks0 and toks0[-1] in MOUSE_ACTIONS:
        for m in toks0[:-1]:
            if m not in MOUSE_MODS:
                raise ValueError(f"modificador inválido para mouse: '{m}'")
        return ("mouse", (toks0[-1], toks0[:-1]))
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
    if kind == "mouse":
        action, mods = data
        md = [0] * 17
        md[0] = 1
        md[1] = 4
        if mods:
            md[4] = MOUSE_MODS[mods[0]]
        what, val = MOUSE_ACTIONS[action]
        if what == "click":
            md[7] = val
        else:
            md[16] = val & 0xFF            # +1 / 0xFF (-1)
        return [0x03, 0xFD, slot, layer1, 0x03] + md
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

    def _reconnect(self):
        """Close and re-open the hidraw node (the pad re-enumerates or the
        device goes stale after idle time — this heals it)."""
        self.close()
        path, caps = find_target()
        if not path:
            return "No se encontró el pad 514c:8850"
        self.h = _open(path)
        if not self.h:
            return "No se pudo reabrir el device"
        self.path = path
        return None

    def send(self, payload):
        msg = bytes(payload) + b"\x00" * (OUT_LEN - len(payload))
        _, err = sync_transfer(self.h, True, msg)
        if err:
            # one automatic recovery attempt: re-open and retry once
            self._reconnect()
            if self.h:
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
        if self.send([0x03, 0xFD, 0xFE, 0xFF]):
            raise RuntimeError("LED commit failed")

    def led_colors(self, layer0, mode, colors):
        """spokino LED record with per-LED colors. colors = 17 (r,g,b) tuplas."""
        if len(colors) != 17:
            raise ValueError("necesito exactamente 17 colores")
        if self.send([0x03, 0xFB, 0xFB, 0xFB]):
            raise RuntimeError("preamble write failed")
        pkt = [0x03, 0xFE, 0xB0, layer0 & 0xFF, mode & 0xFF]
        for (r, g, b) in colors:
            pkt += [r & 0xFF, g & 0xFF, b & 0xFF]
        if self.send(pkt):
            raise RuntimeError("LED record write failed")
        if self.send([0x03, 0xFD, 0xFE, 0xFF]):
            raise RuntimeError("LED commit failed")

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

SETTINGS_PATH = os.path.expanduser("~/.config/macropad-configurador/settings.json")
