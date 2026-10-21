"""MacroPad 514c:8850 — Configurador (teclas + knobs + LEDs) — port Linux/Fedora.

Same tkinter app and same verified protocol as windows/macropad_configurator.py;
the transport is hidraw instead of the Windows HID stack.

Key/knob writes use the ch57x-3 (0xFD) format. Knob slots base 16 verified:
  knob1 = 16/17/18 (ccw/press/cw), knob2 = 19/20/21. Knobs have no read-back.
LED writes use the spokino format:
  preamble 03 FB FB FB, then 03 FE B0 <layer0> <mode> + 17 RGB triplets,
  followed by the commit 03 FD FE FF.

Never sends 0xEF (bootloader) or 0xFC (hardware variant).
Requires linux/60-ch57x-keypad.rules (udev) for hidraw permission.
"""

import errno
import glob
import json
import math
import os
import queue
import select
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

LOCAL_PROFILE = os.path.expanduser("~/.config/macropad-configurador/perfil-514c-8850.json")

OUT_LEN = 65
IN_LEN = 65
TARGET_VID = 0x514C
TARGET_PID = 0x8850

# ----------------------------------------------------------------------------
# HID helpers (hidraw)
# ----------------------------------------------------------------------------


def _hidraw_nodes():
    """[(devpath, vid, pid)] for every hidraw with a numeric HID_ID."""
    out = []
    for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(path, "device", "uevent")) as fh:
                ue = fh.read()
        except OSError:
            continue
        hid_id = next((l for l in ue.splitlines() if l.startswith("HID_ID=")), None)
        if not hid_id:
            continue
        try:
            # HID_ID=bus:vid:pid (3 fields on modern kernels, e.g. 0003:0000514C:00008850)
            parts = hid_id.split("=")[1].split(":")
            if len(parts) < 3:
                continue
            vid, pid = int(parts[1], 16), int(parts[2], 16)
        except ValueError:
            continue
        out.append(("/dev/" + os.path.basename(path), vid, pid))
    return out


def find_target():
    """Return the pad's vendor config hidraw (interface 0). Filters by VID/PID
    plus bInterfaceNumber==00 — simpler and more reliable than parsing the
    report descriptor (the 0xFF00/0x01 collection is there, but short-item
    decoding of 0xFF00 usage pages is brittle across kernels/firmware)."""
    for node, vid, pid in _hidraw_nodes():
        if vid == TARGET_VID and pid == TARGET_PID:
            try:
                dev = os.path.realpath(f"/sys/class/hidraw/{os.path.basename(node)}/device")
                with open(os.path.join(dev, os.pardir, "bInterfaceNumber")) as fh:
                    iface = fh.read().strip()
            except OSError:
                iface = "?"
            if iface == "00":
                return node, None
    return None, None


def _open(path):
    return os.open(path, os.O_RDWR)


def sync_transfer(fd, is_write, payload, timeout_ms=3000):
    """hidraw read/write. For writes payload is already 65 bytes with the
    report id (0x03) first. For reads, return one HID report (report id
    included, same layout Windows returns). Returns (data, err)."""
    try:
        if is_write:
            buf = payload if len(payload) >= OUT_LEN else payload + b"\x00" * (OUT_LEN - len(payload))
            # The pad may stop draining its input queue (idle timeout) and a
            # blocking os.write would hang the UI forever. Bound it like reads.
            _, w, _ = select.select([], [fd], [], 0.5)
            if not w:
                return (None, 1)
            n = os.write(fd, buf)
            return (None, 0) if n else (None, 1)
        r, _, _ = select.select([fd], [], [], timeout_ms / 1000.0)
        if not r:
            return (None, 1)
        data = os.read(fd, IN_LEN)
        return (data, 0)
    except OSError:
        return (None, 1)


def hsv2rgb(h, s, v):
    """Hue 0-360, s/v 0..1 -> (r,g,b) 0..255."""
    h = (h % 360) / 60.0
    i = int(h)
    f = h - i
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))
    r, g, b = ((v, t, p), (q, v, p), (p, v, t),
               (p, q, v), (t, p, v), (v, p, q))[i % 6]
    return int(r * 255), int(g * 255), int(b * 255)



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
        if not h:
            return "No se pudo abrir el device (%s)" % os.strerror(errno.EACCES)
        self.h, self.path = h, path
        return None

    def close(self):
        if self.h:
            os.close(self.h)
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

THEMES = {
    "light": {
        "bg": "#f4f5f9", "card": "#ffffff", "border": "#e6e8f1",
        "fg": "#1b1f27", "muted": "#8992a5",
        "accent": "#5b6cff", "accent_fg": "#ffffff",
        "key": "#fbfbfe", "key_border": "#e6e8f1", "input": "#ffffff",
        "tabbar": "#e9ebf2", "tab_on": "#ffffff",
        "pill_bg": "#e7f8ef", "pill_fg": "#168a52",
        "accent_soft": "#e8ebff",
    },
    "dark": {
        "bg": "#14171d", "card": "#1b1f27", "border": "#262c38",
        "fg": "#e8eaf0", "muted": "#8b94a7",
        "accent": "#5eead4", "accent_fg": "#04221d",
        "key": "#20252e", "key_border": "#2e3542", "input": "#20252e",
        "tabbar": "#0f1217", "tab_on": "#1b1f27",
        "pill_bg": "#123a34", "pill_fg": "#5eead4",
        "accent_soft": "#14312c",
    },
}


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("MacroPad 514c:8850")
        self.geometry("820x900")
        self.minsize(760, 800)
        self.resizable(True, True)
        self.pad = Pad()
        self.logq = queue.Queue()
        self.profile = {1: {}, 2: {}, 3: {}}
        self._pk = self._pn = None
        self._capture = None
        self._led_keys = {}
        self._cur_rgb = (255, 255, 255)
        self._hue, self._sat, self._val = 0.0, 0.0, 1.0
        self._themed = []                      # (widget, kind, extra)
        self._nav_buttons = {}
        self._layer_chips = {}
        self._mode_chips = []

        self.var_layer = tk.IntVar(value=1)
        self.var_knob_layer = tk.IntVar(value=1)
        self.var_led_layer = tk.IntVar(value=0)
        self.var_all_led = tk.BooleanVar(value=False)
        self.var_mode = tk.IntVar(value=1)
        self.var_quick = tk.StringVar()
        self.quick_mods = {}

        self.theme_name = self._load_theme()
        self.T = THEMES[self.theme_name]

        self._build()
        self._apply_theme()
        self._show_page("keys")
        self.after(100, self._drain_logs)
        self.connect_device()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------- settings
    def _load_settings(self):
        try:
            with open(SETTINGS_PATH) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _load_theme(self):
        name = self._load_settings().get("theme", "light")
        return name if name in THEMES else "light"

    def _save_theme(self):
        s = self._load_settings()
        s["theme"] = self.theme_name
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            with open(SETTINGS_PATH, "w") as f:
                json.dump(s, f, indent=1)
        except OSError:
            pass

    # ---------------------------------------------------------------- theme
    def _reg(self, widget, kind, extra=None):
        self._themed.append((widget, kind, extra))
        return widget

    def _style_widget(self, w, kind, extra=None):
        t = self.T
        try:
            if kind in ("bg",):
                w.configure(bg=t["bg"])
            elif kind == "label":
                w.configure(bg=t["bg"], fg=t["fg"])
            elif kind == "muted":
                w.configure(bg=t["bg"], fg=t["muted"])
            elif kind == "card":
                w.configure(bg=t["card"], highlightbackground=t["border"],
                            highlightthickness=1, bd=0)
            elif kind == "card_label":
                w.configure(bg=t["card"], fg=t["fg"])
            elif kind == "card_muted":
                w.configure(bg=t["card"], fg=t["muted"])
            elif kind == "card_title":
                w.configure(bg=t["card"], fg=t["muted"])
            elif kind == "key":
                w.configure(bg=t["key"], fg=t["fg"], activebackground=t["accent_soft"],
                            activeforeground=t["fg"], highlightbackground=t["key_border"],
                            highlightthickness=1, bd=0, relief="flat")
            elif kind == "btn":
                w.configure(bg=t["card"], fg=t["fg"], activebackground=t["accent_soft"],
                            activeforeground=t["fg"], highlightbackground=t["border"],
                            highlightthickness=1, bd=0, relief="flat")
            elif kind == "primary":
                w.configure(bg=t["accent"], fg=t["accent_fg"],
                            activebackground=t["accent"], activeforeground=t["accent_fg"],
                            highlightthickness=0, bd=0, relief="flat")
            elif kind == "ghost":
                w.configure(bg=t["card"], fg=t["muted"], activebackground=t["accent_soft"],
                            activeforeground=t["fg"], highlightthickness=0, bd=0,
                            relief="flat")
            elif kind == "tab_active":
                w.configure(bg=t["tab_on"], fg=t["fg"], activebackground=t["tab_on"],
                            activeforeground=t["fg"], highlightthickness=0, bd=0,
                            relief="flat")
            elif kind == "tab_idle":
                w.configure(bg=t["tabbar"], fg=t["muted"], activebackground=t["tab_on"],
                            activeforeground=t["fg"], highlightthickness=0, bd=0,
                            relief="flat")
            elif kind == "tabbar":
                w.configure(bg=t["tabbar"])
            elif kind == "entry":
                w.configure(bg=t["input"], fg=t["fg"], insertbackground=t["fg"],
                            highlightbackground=t["border"], highlightthickness=1,
                            bd=0, relief="flat")
            elif kind == "canvas":
                w.configure(bg=t["card"], highlightthickness=0)
            elif kind == "text":
                w.configure(bg=t["card"], fg=t["fg"], insertbackground=t["fg"],
                            selectbackground=t["accent"], relief="flat", bd=0)
            elif kind == "pill":
                w.configure(bg=t["pill_bg"], fg=t["pill_fg"])
            elif kind in ("radio", "check"):
                w.configure(bg=t["card"], fg=t["fg"], activebackground=t["card"],
                            activeforeground=t["fg"], selectcolor=t["card"],
                            highlightthickness=0, bd=0)
            elif kind == "chip":
                w.configure(bg=t["card"], fg=t["fg"], activebackground=t["accent_soft"],
                            activeforeground=t["fg"], highlightbackground=t["border"],
                            highlightthickness=1, bd=0, relief="flat")
            elif kind == "chip_on":
                w.configure(bg=t["accent"], fg=t["accent_fg"],
                            activebackground=t["accent"], activeforeground=t["accent_fg"],
                            highlightbackground=t["accent"], highlightthickness=1,
                            bd=0, relief="flat")
        except tk.TclError:
            pass

    def _apply_theme(self):
        self.configure(bg=self.T["bg"])
        for w, kind, extra in self._themed:
            self._style_widget(w, kind, extra)
        self._refresh_chips()
        self._paint_swatch()
        try:
            self._hue_draw()
            self._sv_draw()
        except AttributeError:
            pass
        self._led_key_refresh()

    def toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.T = THEMES[self.theme_name]
        self._save_theme()
        self._apply_theme()
        self.btn_theme.configure(text="☀" if self.theme_name == "dark" else "🌙")
        self.log_msg(f"Tema: {self.theme_name}")

    # ------------------------------------------------------------- estructura
    def _build(self):
        t = self.T
        outer = self._reg(tk.Frame(self), "bg")
        outer.pack(fill="both", expand=True, padx=18, pady=14)

        # top bar
        top = self._reg(tk.Frame(outer), "bg")
        top.pack(fill="x")
        self._reg(tk.Label(top, text="MacroPad 514c:8850",
                           font=("DejaVu Sans", 17, "bold")), "label").pack(side="left")
        self.lbl_pill = self._reg(tk.Label(top, text="  ● conectando  ",
                                           font=("DejaVu Sans", 9, "bold")), "pill")
        self.lbl_pill.pack(side="left", padx=12)
        self.btn_theme = self._reg(tk.Button(
            top, text="🌙" if self.theme_name == "light" else "☀",
            command=self.toggle_theme, font=("DejaVu Sans", 12),
            padx=10, pady=4, cursor="hand2"), "btn")
        self.btn_theme.pack(side="right")
        self.btn_conn = self._reg(tk.Button(
            top, text="Conectar", command=self.connect_device,
            font=("DejaVu Sans", 10, "bold"), padx=12, pady=5, cursor="hand2"),
            "primary")
        self.btn_conn.pack(side="right", padx=8)

        # nav pills
        self.navbar = self._reg(tk.Frame(outer), "tabbar")
        self.navbar.pack(fill="x", pady=(14, 8), ipady=3)
        for key, label in (("keys", "Teclas"), ("knobs", "Knobs"),
                           ("leds", "LEDs"), ("device", "Dispositivo")):
            b = tk.Button(self.navbar, text=label, command=lambda k=key: self._show_page(k),
                          font=("DejaVu Sans", 10, "bold"), padx=16, pady=5, cursor="hand2")
            b.pack(side="left", padx=(6, 0), pady=2)
            self._reg(b, "tab_idle")
            self._nav_buttons[key] = b

        # pages
        self.content = self._reg(tk.Frame(outer), "bg")
        self.content.pack(fill="both", expand=True)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.pages = {}
        for key in ("keys", "knobs", "leds", "device"):
            p = self._reg(tk.Frame(self.content), "bg")
            p.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = p
        self._build_keys_page(self.pages["keys"])
        self._build_knobs_page(self.pages["knobs"])
        self._build_leds_page(self.pages["leds"])
        self._build_device_page(self.pages["device"])

        # log
        logcard = self._reg(tk.Frame(outer), "card")
        logcard.pack(fill="x", pady=(10, 0))
        self._reg(tk.Label(logcard, text="LOG", font=("DejaVu Sans", 8, "bold")),
                  "card_title").pack(anchor="w", padx=12, pady=(8, 0))
        self.log = self._reg(tk.Text(logcard, height=4, font=("DejaVu Sans Mono", 8)),
                             "text")
        self.log.configure(state="disabled")
        self.log.pack(fill="x", padx=12, pady=(2, 10))

    def _show_page(self, key):
        self.pages[key].tkraise()
        for k, b in self._nav_buttons.items():
            self._style_widget(b, "tab_active" if k == key else "tab_idle")

    def _card(self, parent, title=None):
        c = self._reg(tk.Frame(parent), "card")
        c.pack(fill="x", pady=(0, 10))
        if title:
            self._reg(tk.Label(c, text=title.upper(), font=("DejaVu Sans", 8, "bold")),
                      "card_title").pack(anchor="w", padx=14, pady=(10, 4))
            inner = self._reg(tk.Frame(c), "card")
            inner.pack(fill="x", padx=14, pady=(0, 12))
            return inner
        return c

    # --------------------------------------------------------------- páginas
    def _build_keys_page(self, parent):
        # capa
        c = self._card(parent, "Capa de teclas")
        row = self._reg(tk.Frame(c), "card")
        row.pack(fill="x")
        self._layer_chips["keys"] = self._chips(row, self.var_layer, (1, 2, 3),
                                                self._refresh_keys, prefix="Capa ")

        # rápida
        c = self._card(parent, "Asignación rápida (opcional)")
        qrow = self._reg(tk.Frame(c), "card")
        qrow.pack(fill="x")
        self.quick_entry = self._reg(tk.Entry(qrow, textvariable=self.var_quick,
                                              font=("DejaVu Sans", 10)), "entry")
        self.quick_entry.grid(row=0, column=0, columnspan=8, sticky="we", ipady=4)
        qrow.columnconfigure(0, weight=1)
        self._reg(tk.Button(qrow, text="Limpiar", command=self._quick_clear,
                            font=("DejaVu Sans", 9), padx=10, cursor="hand2"),
                  "btn").grid(row=0, column=8, padx=(8, 0), sticky="e")
        for i, m in enumerate(("ctrl", "shift", "alt", "win", "rctrl", "rshift", "ralt", "rwin")):
            v = tk.BooleanVar(value=False)
            self.quick_mods[m] = v
            self._reg(tk.Checkbutton(qrow, text=m, variable=v,
                                     font=("DejaVu Sans", 9)), "check").grid(
                row=1, column=i, sticky="w", padx=(0, 4), pady=(6, 0))
        media = [("Vol+", "volumeup"), ("Vol−", "volumedown"), ("Mute", "mute"),
                 ("Play", "play"), ("Next", "next"), ("Prev", "prev"), ("Stop", "stop")]
        self._reg(tk.Label(qrow, text="media", font=("DejaVu Sans", 8, "bold")),
                  "card_muted").grid(row=2, column=0, sticky="w", pady=(8, 0))
        for i, (lbl, name) in enumerate(media):
            self._reg(tk.Button(qrow, text=lbl, width=6, cursor="hand2",
                                command=lambda n=name: self._media_quick(n),
                                font=("DejaVu Sans", 8), padx=4, pady=2), "btn").grid(
                row=2, column=1 + i, padx=(4, 0), pady=(8, 0))
        self._reg(tk.Label(c, text="Con un macro en la barra, el clic en una tecla lo asigna "
                                   "directo. Con la barra vacía, el clic activa la captura "
                                   "de input (tecla, combo o mouse · ESC cancela).",
                           font=("DejaVu Sans", 8), wraplength=700, justify="left"),
                  "card_muted").pack(anchor="w", pady=(8, 0))

        # teclas
        c = self._card(parent, "Teclas 1..12")
        gridf = self._reg(tk.Frame(c), "card")
        gridf.pack()
        self.key_buttons = {}
        for pos in range(12):
            r, col = divmod(pos, 4)
            b = tk.Button(gridf, text="", width=12, height=3, font=("DejaVu Sans", 9),
                          cursor="hand2",
                          command=lambda p=pos: self.edit_cell(SLOT_BY_POS_ROT[p],
                                                               f"K{p + 1}"))
            b.grid(row=r, column=col, padx=5, pady=4)
            self._reg(b, "key")
            self.key_buttons[pos] = b

        btns = self._reg(tk.Frame(parent), "bg")
        btns.pack(fill="x", pady=(4, 0))
        self._reg(tk.Button(btns, text="Aplicar teclas (capa)", command=self.apply_keys_layer,
                            font=("DejaVu Sans", 9, "bold"), padx=14, pady=6,
                            cursor="hand2"), "primary").pack(side="left", padx=(0, 8))
        self._reg(tk.Button(btns, text="Aplicar todas las capas", command=self.apply_keys_all,
                            font=("DejaVu Sans", 9), padx=12, pady=6, cursor="hand2"),
                  "btn").pack(side="left", padx=8)
        self._reg(tk.Button(btns, text="Vaciar capa", command=self.clear_keys_layer,
                            font=("DejaVu Sans", 9), padx=12, pady=6, cursor="hand2"),
                  "ghost").pack(side="left", padx=8)

    def _build_knobs_page(self, parent):
        c = self._card(parent, "Capa de knobs")
        row = self._reg(tk.Frame(c), "card")
        row.pack(fill="x")
        self._layer_chips["knobs"] = self._chips(row, self.var_knob_layer, (1, 2, 3),
                                                 self._refresh_knobs, prefix="Capa ")
        self._reg(tk.Label(c, text="El firmware no reporta los knobs: se guardan en el perfil "
                                   "local y se restauran al conectar.",
                           font=("DejaVu Sans", 8)), "card_muted").pack(anchor="w", pady=(8, 0))

        row = self._reg(tk.Frame(parent), "bg")
        row.pack(fill="both", expand=True)
        self.knob_canvases = {}
        self.knob_texts = {}
        for col, k in enumerate((0, 1)):
            box = self._reg(tk.Frame(row), "card")
            box.grid(row=0, column=col, padx=(0 if col == 0 else 12, 0), sticky="n")
            self._reg(tk.Label(box, text=f"KNOB {k + 1}",
                               font=("DejaVu Sans", 9, "bold")), "card_title").pack(
                anchor="w", padx=14, pady=(10, 0))
            cv = self._reg(tk.Canvas(box, width=190, height=240), "canvas")
            cv.pack(padx=10, pady=8)
            cv.bind("<Button-1>", lambda e, kk=k: self._knob_click(kk, e.x, e.y))
            self.knob_canvases[k] = cv
            self._draw_knob(k, cv)
        self._reg(tk.Label(parent, text="Clic en el knob: zona izquierda / centro / derecha "
                                        "para capturar el input de esa acción.",
                           font=("DejaVu Sans", 8)), "muted").pack(anchor="w", pady=(2, 8))

        btns = self._reg(tk.Frame(parent), "bg")
        btns.pack(fill="x", pady=(4, 0))
        self._reg(tk.Button(btns, text="Aplicar knobs (capa)", command=self.apply_knobs_layer,
                            font=("DejaVu Sans", 9, "bold"), padx=14, pady=6,
                            cursor="hand2"), "primary").pack(side="left", padx=(0, 8))
        self._reg(tk.Button(btns, text="Aplicar todas las capas", command=self.apply_knobs_all,
                            font=("DejaVu Sans", 9), padx=12, pady=6, cursor="hand2"),
                  "btn").pack(side="left", padx=8)
        self._reg(tk.Button(btns, text="Vaciar capa", command=self.clear_knobs_layer,
                            font=("DejaVu Sans", 9), padx=12, pady=6, cursor="hand2"),
                  "ghost").pack(side="left", padx=8)

    def _build_leds_page(self, parent):
        c = self._card(parent, "Capa del LED")
        row = self._reg(tk.Frame(c), "card")
        row.pack(fill="x")
        self._layer_chips["led"] = self._chips(row, self.var_led_layer, (0, 1, 2),
                                               self._led_layer_changed,
                                               labels=("Capa 1", "Capa 2", "Capa 3"))
        self._reg(tk.Checkbutton(c, text="Aplicar a TODAS las capas",
                                 variable=self.var_all_led,
                                 command=self._led_layer_changed,
                                 font=("DejaVu Sans", 9)), "check").pack(
            anchor="w", pady=(8, 0))
        self._reg(tk.Label(c, text="Por defecto se aplica solo a la capa elegida.",
                           font=("DejaVu Sans", 8)), "card_muted").pack(anchor="w")

        c = self._card(parent, "Modo")
        mrow = self._reg(tk.Frame(c), "card")
        mrow.pack(fill="x")
        for i, (label, val) in enumerate(MODES):
            b = tk.Button(mrow, text=label, font=("DejaVu Sans", 9), padx=10, pady=3,
                          cursor="hand2",
                          command=lambda v=val: self._set_mode(v))
            b.grid(row=i // 4, column=i % 4, padx=(0, 6), pady=3, sticky="w")
            self._reg(b, "chip")
            self._mode_chips.append((b, val))

        c = self._card(parent, "Color y LED por tecla (modo Fijo)")
        row3 = self._reg(tk.Frame(c), "card")
        row3.pack(fill="x")
        picker = self._reg(tk.Frame(row3), "card")
        picker.grid(row=0, column=0, padx=(0, 16))
        self.hue_bar = self._reg(tk.Canvas(picker, width=190, height=16), "canvas")
        self.hue_bar.pack()
        self.sv_square = self._reg(tk.Canvas(picker, width=190, height=120), "canvas")
        self.sv_square.pack(pady=(4, 0))
        for cv, fn in ((self.hue_bar, self._hue_pick), (self.sv_square, self._sv_pick)):
            cv.bind("<Button-1>", fn)
            cv.bind("<B1-Motion>", fn)
        side = self._reg(tk.Frame(row3), "card")
        side.grid(row=0, column=1, padx=(0, 18), sticky="n")
        self.swatch = self._reg(tk.Canvas(side, width=52, height=34), "canvas")
        self.swatch.pack(anchor="w")
        self._reg(tk.Label(side, text="Hue arriba\nS/B abajo\n\nClic en tecla: asigna\n"
                                      "Clic derecho: quita",
                           font=("DejaVu Sans", 8), justify="left"),
                  "card_muted").pack(anchor="w", pady=(8, 0))

        kf = self._reg(tk.Frame(row3), "card")
        kf.grid(row=0, column=2, sticky="n")
        self.led_key_buttons = {}
        for pos in range(12):
            r, col = divmod(pos, 4)
            b = tk.Button(kf, text=f"K{pos + 1}", width=4, font=("DejaVu Sans", 8),
                          relief="flat", bd=0, cursor="hand2")
            b.grid(row=r, column=col, padx=2, pady=2)
            b.bind("<Button-1>", lambda e, p=pos: self._led_key_click(p))
            b.bind("<Button-3>", lambda e, p=pos: self._led_key_clear(p))
            self._reg(b, "key")
            self.led_key_buttons[pos] = b

        btns = self._reg(tk.Frame(parent), "bg")
        btns.pack(fill="x", pady=(4, 0))
        self._reg(tk.Button(btns, text="Aplicar LED", command=self.apply_led,
                            font=("DejaVu Sans", 9, "bold"), padx=14, pady=6,
                            cursor="hand2"), "primary").pack(side="left", padx=(0, 8))
        self._reg(tk.Button(btns, text="Apagar LED", command=self.off_led,
                            font=("DejaVu Sans", 9), padx=12, pady=6, cursor="hand2"),
                  "btn").pack(side="left", padx=8)
        self._reg(tk.Button(btns, text="Test", command=self.test_led,
                            font=("DejaVu Sans", 9), padx=12, pady=6, cursor="hand2"),
                  "ghost").pack(side="left", padx=8)

        self._hue_draw()
        self._sv_draw()
        self._paint_swatch()
        self._led_key_refresh()

    def _build_device_page(self, parent):
        c = self._card(parent, "Dispositivo")
        self.lbl_device = self._reg(tk.Label(c, text="—", font=("DejaVu Sans", 9)),
                                    "card_muted")
        self.lbl_device.pack(anchor="w")

        c = self._card(parent, "Acciones globales")
        self._reg(tk.Button(c, text="Escribir TODO (teclas + knobs, 3 capas)",
                            command=self.write_all, font=("DejaVu Sans", 9, "bold"),
                            padx=14, pady=6, cursor="hand2"), "primary").pack(anchor="w")
        self._reg(tk.Label(c, text="Los botones de cada pestaña aplican solo su dominio "
                                   "y su capa.", font=("DejaVu Sans", 8)),
                  "card_muted").pack(anchor="w", pady=(8, 0))

        c = self._card(parent, "Perfil local (los knobs no se leen del pad)")
        self._reg(tk.Label(c, text=LOCAL_PROFILE, font=("DejaVu Sans", 8)),
                  "card_muted").pack(anchor="w")
        row = self._reg(tk.Frame(c), "card")
        row.pack(anchor="w", pady=(8, 0))
        self._reg(tk.Button(row, text="Guardar ahora", command=self._save_local_profile,
                            font=("DejaVu Sans", 9), padx=10, pady=4, cursor="hand2"),
                  "btn").pack(side="left", padx=(0, 8))
        self._reg(tk.Button(row, text="Recargar del archivo",
                            command=self._reload_local_profile,
                            font=("DejaVu Sans", 9), padx=10, pady=4, cursor="hand2"),
                  "btn").pack(side="left")

    # ---------------------------------------------------------------- chips
    def _chips(self, parent, var, values, refresh, prefix="", labels=None):
        buttons = []
        for i, v in enumerate(values):
            lbl = labels[i] if labels else f"{prefix}{v}"
            b = tk.Button(parent, text=lbl, font=("DejaVu Sans", 9, "bold"),
                          padx=12, pady=4, cursor="hand2",
                          command=lambda vv=v: self._set_chip(var, vv, refresh))
            b.pack(side="left", padx=(0, 6))
            self._reg(b, "chip")
            buttons.append((b, v))
        self._apply_chip_state(buttons, var.get())
        return buttons

    def _set_chip(self, var, value, refresh):
        var.set(value)
        chips = (self._layer_chips.get("keys") if var is self.var_layer else
                 self._layer_chips.get("knobs") if var is self.var_knob_layer else
                 self._layer_chips.get("led"))
        self._apply_chip_state(chips, value)
        refresh()

    def _apply_chip_state(self, chips, value):
        for b, v in chips or []:
            self._style_widget(b, "chip_on" if v == value else "chip")

    def _refresh_chips(self):
        self._apply_chip_state(self._layer_chips.get("keys"), self.var_layer.get())
        self._apply_chip_state(self._layer_chips.get("knobs"), self.var_knob_layer.get())
        self._apply_chip_state(self._layer_chips.get("led"), self.var_led_layer.get())
        for b, v in self._mode_chips:
            self._style_widget(b, "chip_on" if v == self.var_mode.get() else "chip")

    def _set_mode(self, val):
        self.var_mode.set(val)
        self._refresh_chips()
        self._on_mode_change()

    # ----------------------------------------------------------- refrescos
    def _refresh_keys(self):
        layer = self.var_layer.get()
        for pos in range(12):
            slot = SLOT_BY_POS_ROT[pos]
            txt = self.profile[layer].get(slot)
            self.key_buttons[pos].configure(text=f"K{pos + 1}\n{(txt or '—')[:14]}")

    def _draw_knob(self, k, cv):
        t = self.T
        cv.delete("all")
        cx, cy = 95, 58
        cv.create_oval(cx - 42, cy - 42, cx + 42, cy + 42, fill=t["key"],
                       outline=t["border"], width=2)
        cv.create_oval(cx - 22, cy - 22, cx + 22, cy + 22, fill=t["card"],
                       outline=t["border"], width=1)
        cv.create_line(cx, cy, cx, cy - 19, fill=t["accent"], width=5, capstyle="round")
        cv.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=t["bg"], outline=t["border"])
        self.knob_texts[k] = cv.create_text(
            95, 122, text="", fill=t["fg"], justify="center", anchor="n",
            font=("DejaVu Sans", 10))

    def _knob_click(self, k, x, y):
        cx, cy = 95, 58
        dx, dy = x - cx, y - cy
        r = math.hypot(dx, dy)
        if r > 52:
            return
        if r < 22:
            act = "press"
        elif dx < -8:
            act = "ccw"
        elif dx > 8:
            act = "cw"
        else:
            act = "press"
        slot = KNOB_BASE + 3 * k + KNOB_ACTIONS.index(act)
        self.edit_cell(slot, f"Knob {k + 1} {act}")

    def _refresh_knobs(self):
        layer = self.var_knob_layer.get()
        for k in range(2):
            item = self.knob_texts.get(k)
            if item is None:
                continue
            lines = []
            for a, act in enumerate(KNOB_ACTIONS):
                slot = KNOB_BASE + 3 * k + a
                txt = self.profile[layer].get(slot)
                label = {"ccw": "izq", "press": "undir", "cw": "der"}[act]
                lines.append(f"{label}: {(txt or '—')[:12]}")
            self.knob_canvases[k].itemconfigure(item, text="\n".join(lines))

    # --------------------------------------------------------------- captura
    MOD_KEYSYMS = {"Control_L", "Control_R", "Shift_L", "Shift_R",
                   "Alt_L", "Alt_R", "Super_L", "Super_R", "Meta_L", "Meta_R"}

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

    def _media_quick(self, name):
        self.var_quick.set(name)
        for v in self.quick_mods.values():
            v.set(False)

    def edit_cell(self, slot, label):
        quick = self._quick_macro()
        if quick is not None:
            self.profile[self._slot_layer(slot)][slot] = quick
            self._refresh_domain(slot)
            self._save_local_profile()
            self.log_msg(f"{label} ← {quick} (barra rápida)")
            return
        self._start_capture(slot, label)

    def _slot_layer(self, slot):
        return self.var_layer.get() if slot <= 12 else self.var_knob_layer.get()

    def _refresh_domain(self, slot):
        self._refresh_keys() if slot <= 12 else self._refresh_knobs()

    def _start_capture(self, slot, label):
        self._capture = (slot, label)
        self.lbl_pill.configure(text="  ⏺ capturando…  ")
        self.log_msg(f"Captura para {label}: apretá tecla o botón (ESC cancela)")
        self.bind_all("<KeyPress>", self._capture_key)
        self.bind_all("<ButtonPress>", self._capture_button)
        self.bind_all("<MouseWheel>", self._capture_wheel)

    def _end_capture(self, msg=None):
        self._capture = None
        for seq in ("<KeyPress>", "<ButtonPress>", "<MouseWheel>"):
            try:
                self.unbind_all(seq)
            except tk.TclError:
                pass
        self._set_status()
        if msg:
            self.log_msg(msg)

    def _set_status(self):
        if self.pad.h:
            self.lbl_pill.configure(text="  ● conectado  ")
        else:
            self.lbl_pill.configure(text="  ○ sin dispositivo  ")

    def _mods_from_state(self, state):
        if sys.platform.startswith("linux"):
            mask = (("ctrl", 0x4), ("shift", 0x1), ("alt", 0x8), ("win", 0x40))
        else:
            mask = (("ctrl", 0x4), ("shift", 0x1), ("alt", 0x20000))
        return [name for name, bit in mask if state & bit]

    def _keysym_to_name(self, keysym):
        ks = keysym.lower()
        special = {"return": "enter", "kp_enter": "enter", "escape": "esc",
                   "backspace": "backspace", "tab": "tab", "space": "space",
                   "insert": "insert", "delete": "delete", "home": "home",
                   "end": "end", "prior": "pageup", "next": "pagedown",
                   "left": "left", "right": "right", "up": "up", "down": "down",
                   "minus": "minus", "equal": "equal"}
        if ks in special:
            return special[ks]
        media = {"xf86audioraisevolume": "volumeup", "xf86audiolowervolume": "volumedown",
                 "xf86audiomute": "mute", "xf86audioplay": "play",
                 "xf86audionext": "next", "xf86audioprev": "prev", "xf86audiostop": "stop"}
        if ks in media:
            return media[ks]
        if ks in KEYCODES:
            return ks
        return None

    def _capture_key(self, event):
        if event.keysym in self.MOD_KEYSYMS:
            return
        if event.keysym == "Escape":
            self._end_capture("captura cancelada")
            return
        name = self._keysym_to_name(event.keysym)
        if name is None:
            self.log_msg(f"tecla no soportada: {event.keysym}")
            return
        mods = self._mods_from_state(event.state)
        self._finish_capture("-".join(mods + [name]) if mods else name)

    def _capture_button(self, event):
        name = {1: "click", 2: "mclick", 3: "rclick",
                4: "wheelup", 5: "wheeldown"}.get(event.num)
        if name is None:
            return
        mods = [m for m in self._mods_from_state(event.state) if m in MOUSE_MODS]
        self._finish_capture("-".join(mods + [name]) if mods else name)

    def _capture_wheel(self, event):
        name = "wheelup" if event.delta > 0 else "wheeldown"
        mods = [m for m in self._mods_from_state(event.state) if m in MOUSE_MODS]
        self._finish_capture("-".join(mods + [name]) if mods else name)

    def _finish_capture(self, text):
        slot, label = self._capture
        try:
            parse_binding(text)
        except ValueError as e:
            self.log_msg(f"ERROR: {e}")
            return
        self.profile[self._slot_layer(slot)][slot] = text
        self._refresh_domain(slot)
        self._save_local_profile()
        self._end_capture(f"{label} ← {text}")

    # -------------------------------------------------------------- escritura
    KEYS_SLOTS = list(SLOT_BY_POS_ROT)
    KNOBS_SLOTS = [KNOB_BASE + 3 * k + a for k in range(2) for a in range(3)]

    def _write_slots(self, slots, layer):
        if not self.pad.h:
            self.log_msg("No hay dispositivo conectado.")
            return 0
        n = 0
        for slot in slots:
            try:
                self.pad.write_binding(slot, layer,
                                       self.profile[layer].get(slot, "borrar"))
                n += 1
            except (RuntimeError, ValueError) as e:
                self.log_msg(f"ERROR slot {slot}: {e}")
                return n
        return n

    def apply_keys_layer(self):
        n = self._write_slots(self.KEYS_SLOTS, self.var_layer.get())
        self._save_local_profile()
        self.log_msg(f"Teclas capa {self.var_layer.get()} escritas ({n})")

    def apply_keys_all(self):
        for layer in (1, 2, 3):
            self._write_slots(self.KEYS_SLOTS, layer)
        self._save_local_profile()
        self.log_msg("Teclas escritas en las 3 capas")

    def apply_knobs_layer(self):
        n = self._write_slots(self.KNOBS_SLOTS, self.var_knob_layer.get())
        self._save_local_profile()
        self.log_msg(f"Knobs capa {self.var_knob_layer.get()} escritos ({n})")

    def apply_knobs_all(self):
        for layer in (1, 2, 3):
            self._write_slots(self.KNOBS_SLOTS, layer)
        self._save_local_profile()
        self.log_msg("Knobs escritos en las 3 capas")

    def clear_keys_layer(self):
        layer = self.var_layer.get()
        for slot in self.KEYS_SLOTS:
            self.profile[layer].pop(slot, None)
        self._refresh_keys()
        self._save_local_profile()
        self._write_slots(self.KEYS_SLOTS, layer)
        self.log_msg(f"Teclas de la capa {layer} vaciadas")

    def clear_knobs_layer(self):
        layer = self.var_knob_layer.get()
        for slot in self.KNOBS_SLOTS:
            self.profile[layer].pop(slot, None)
        self._refresh_knobs()
        self._save_local_profile()
        self._write_slots(self.KNOBS_SLOTS, layer)
        self.log_msg(f"Knobs de la capa {layer} vaciados")

    def write_all(self):
        for layer in (1, 2, 3):
            self._write_slots(self.KEYS_SLOTS, layer)
            self._write_slots(self.KNOBS_SLOTS, layer)
        self._save_local_profile()
        self.log_msg("TODO escrito: teclas + knobs en las 3 capas")

    # ------------------------------------------------------------------ LEDs
    def _on_mode_change(self):
        fijo = self.var_mode.get() == 1
        for w in (self.hue_bar, self.sv_square):
            w.configure(state="normal" if fijo else "disabled")
        for b in self.led_key_buttons.values():
            b.configure(state="normal" if fijo else "disabled")

    def _led_layer_changed(self):
        self._refresh_chips()
        self._led_key_refresh()

    def _hue_draw(self):
        c = self.hue_bar
        c.delete("all")
        w = int(c["width"])
        h = int(c["height"])
        for x in range(w):
            col = "#%02x%02x%02x" % hsv2rgb(x * 360.0 / w, 1.0, 1.0)
            c.create_line(x, 0, x, h, fill=col)
        self._hue_marks()

    def _sv_draw(self):
        c = self.sv_square
        c.delete("all")
        w = int(c["width"])
        h = int(c["height"])
        cell = 4
        for yy in range(0, h, cell):
            v = 1.0 - yy / h
            for xx in range(0, w, cell):
                s = xx / w
                col = "#%02x%02x%02x" % hsv2rgb(self._hue, s, v)
                c.create_rectangle(xx, yy, xx + cell, yy + cell, fill=col, outline=col)
        self._hue_marks()

    def _hue_marks(self):
        c = self.hue_bar
        c.delete("hmark")
        w = int(c["width"])
        h = int(c["height"])
        x = self._hue * w / 360.0
        c.create_rectangle(x - 3, 0, x + 3, h, outline="#ffffff", width=2, tags="hmark")
        c2 = self.sv_square
        c2.delete("smark")
        w2 = int(c2["width"])
        h2 = int(c2["height"])
        mx, my = self._sat * w2, (1.0 - self._val) * h2
        c2.create_rectangle(mx - 5, my - 5, mx + 5, my + 5, outline="#ffffff",
                            width=2, tags="smark")
        c2.create_rectangle(mx - 4, my - 4, mx + 4, my + 4, outline="#000000",
                            width=1, tags="smark")

    def _apply_hsv(self):
        self._cur_rgb = hsv2rgb(self._hue, self._sat, self._val)
        self._paint_swatch()

    def _hue_pick(self, event):
        self._hue = max(0.0, min(360.0, event.x * 360.0 / int(self.hue_bar["width"])))
        self._sv_draw()
        self._apply_hsv()

    def _sv_pick(self, event):
        w = int(self.sv_square["width"])
        h = int(self.sv_square["height"])
        self._sat = max(0.0, min(1.0, event.x / w))
        self._val = max(0.0, min(1.0, 1.0 - event.y / h))
        self._hue_marks()
        self._apply_hsv()

    def _paint_swatch(self, rgb=None):
        if not hasattr(self, "swatch"):
            return
        rgb = rgb or self._cur_rgb
        self.swatch.configure(bg="#%02x%02x%02x" % rgb)

    def _led_layers(self):
        return [0, 1, 2] if self.var_all_led.get() else [self.var_led_layer.get()]

    def _led_key_click(self, pos):
        if self.var_mode.get() != 1:
            self.log_msg("El LED por tecla solo aplica en modo Fijo")
            return
        slot = SLOT_BY_POS_ROT[pos]
        for layer0 in self._led_layers():
            self._led_keys.setdefault(layer0, {})[slot] = self._cur_rgb
        self._led_key_refresh()
        self.apply_led()

    def _led_key_clear(self, pos):
        slot = SLOT_BY_POS_ROT[pos]
        for layer0 in self._led_layers():
            self._led_keys.get(layer0, {}).pop(slot, None)
        self._led_key_refresh()
        self.apply_led()

    def _led_key_refresh(self):
        if not hasattr(self, "led_key_buttons"):
            return
        layer0 = (2 if self.var_all_led.get() else self.var_led_layer.get())
        for pos in range(12):
            slot = SLOT_BY_POS_ROT[pos]
            rgb = self._led_keys.get(layer0, {}).get(slot)
            b = self.led_key_buttons[pos]
            if rgb:
                b.configure(bg="#%02x%02x%02x" % rgb, fg="#ffffff")
            else:
                self._style_widget(b, "key")

    def _led_17(self, layer0):
        DIM = (0, 0, 0)
        cols = [DIM] * 17
        for slot, rgb in self._led_keys.get(layer0, {}).items():
            if 1 <= slot <= 12:
                cols[slot - 1] = rgb
        return cols

    def apply_led(self):
        if not self.pad.h:
            self.log_msg("No hay dispositivo conectado.")
            return
        mode = self.var_mode.get()
        rgb = self._cur_rgb
        try:
            for layer0 in self._led_layers():
                if mode == 1:
                    cols = self._led_17(layer0)
                    if all(sum(c) < 48 for c in cols):
                        self.pad.led_write(layer0, 0, (0, 0, 0))
                    else:
                        self.pad.led_colors(layer0, mode, cols)
                else:
                    self.pad.led_write(layer0, mode, rgb)
        except RuntimeError as e:
            self.log_msg(f"ERROR: {e}")
            return
        self.log_msg(f"LED modo={mode} → capa(s) {self._led_layers()}")

    def off_led(self):
        if not self.pad.h:
            self.log_msg("No hay dispositivo conectado.")
            return
        layers = self._led_layers()
        for layer0 in layers:
            try:
                self.pad.led_write(layer0, 0, (0, 0, 0))
            except RuntimeError as e:
                self.log_msg(f"ERROR: {e}")
                return
            self._led_keys.pop(layer0, None)
        self._led_key_refresh()
        self.log_msg(f"LED apagado en capa(s) {layers}")

    def test_led(self):
        if not self.pad.h:
            self.log_msg("No hay dispositivo conectado.")
            return
        self.log_msg("Test: ciclo de colores...")
        threading.Thread(target=self._test_run, daemon=True).start()

    def _test_run(self):
        try:
            for rgb in [p[1] for p in PRESETS] + [(255, 255, 255)]:
                for layer0 in self._led_layers():
                    self.pad.led_write(layer0, 1, rgb)
                time.sleep(0.25)
            for layer0 in self._led_layers():
                self.pad.led_write(layer0, 1, self._cur_rgb)
            self.log_msg("Test terminado")
        except RuntimeError as e:
            self.log_msg(f"ERROR en test: {e}")

    # ---------------------------------------------------------- persistencia
    def _load_local_profile(self):
        try:
            with open(LOCAL_PROFILE) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_local_profile(self):
        data = {"layers": {str(k): {str(s): t for s, t in v.items()}
                           for k, v in self.profile.items()}}
        try:
            os.makedirs(os.path.dirname(LOCAL_PROFILE), exist_ok=True)
            tmp = LOCAL_PROFILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, LOCAL_PROFILE)
        except OSError as e:
            self.log_msg(f"No pude guardar el perfil local: {e}")

    def _reload_local_profile(self):
        local = self._load_local_profile()
        for layer in (1, 2, 3):
            for slot_s, text in local.get("layers", {}).get(str(layer), {}).items():
                try:
                    slot = int(slot_s)
                except ValueError:
                    continue
                self.profile[layer][slot] = text
        self._refresh_keys()
        self._refresh_knobs()
        self.log_msg("Perfil local recargado (teclas + knobs)")

    # ------------------------------------------------------------- conexión
    def connect_device(self):
        self.log_msg("Conectando...")
        err = self.pad.connect()
        if err:
            self.lbl_pill.configure(text="  ⚠ sin dispositivo  ")
            self.lbl_device.configure(text=err)
            self.log_msg(f"ERROR: {err}")
            return
        keys, knobs = self.pad.identity()
        self._pk, self._pn = keys, knobs
        self._set_status()
        self.lbl_device.configure(text=f"{self.pad.path} · {keys} teclas · {knobs} knobs")
        self.log_msg(f"Conectado: {self.pad.path} ({keys} teclas, {knobs} knobs)")
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
        local = self._load_local_profile()
        for layer in (1, 2, 3):
            for slot_s, text in local.get("layers", {}).get(str(layer), {}).items():
                try:
                    slot = int(slot_s)
                except ValueError:
                    continue
                if slot >= KNOB_BASE:
                    profile[layer][slot] = text
        self.profile = profile
        self._refresh_keys()
        self._refresh_knobs()
        self.log_msg("Perfil cargado: teclas desde el pad, knobs desde el perfil local")

    # ---------------------------------------------------------------- log
    def log_msg(self, text):
        self.logq.put(time.strftime("[%H:%M:%S] ") + str(text))

    def _drain_logs(self):
        try:
            while True:
                msg = self.logq.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", msg + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain_logs)

    def _on_close(self):
        self._save_local_profile()
        self.pad.close()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
