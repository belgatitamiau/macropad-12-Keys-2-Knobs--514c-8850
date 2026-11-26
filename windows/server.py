#!/usr/bin/env python3
"""MacroPad 514c:8850 — backend Windows para la app web.

Sirve el frontend (webapp/index.html) y expone el pad por HTTP:
  GET  /api/state        estado + perfil
  POST /api/connect      reconectar
  POST /api/write        {domain: keys|knobs|all, layer: 1..3|all}
  POST /api/clear        {domain: keys|knobs, layer: 1..3}
  POST /api/led          {layers:[0,1,2], mode, rgb:[r,g,b], colors:[[r,g,b]x17]}
  POST /api/settings     {theme: light|dark}
  GET  /                 frontend

Reutiliza windows/pad_core.py (mismo protocolo verificado, transporte HID de
Windows) y sirve el mismo frontend que la app de Linux (linux/webapp/).
"""
import importlib.util
import json
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
WEBAPP = os.path.join(os.path.dirname(HERE), "linux", "webapp")
PORT = int(os.environ.get("MACROPAD_PORT", "8799"))

# ---------------------------------------------------------------- núcleo
spec = importlib.util.spec_from_file_location("mcore", os.path.join(HERE, "pad_core.py"))
mcore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcore)

P = mcore.Pad()
STATE_LOCK = threading.Lock()
DEVICE = {"keys": None, "knobs": None}

KEYS_SLOTS = list(mcore.SLOT_BY_POS_ROT)
KNOBS_SLOTS = [mcore.KNOB_BASE + 3 * k + a for k in range(2) for a in range(3)]


def default_profile():
    return {1: {}, 2: {}, 3: {}}


PROFILE = default_profile()


def load_local():
    try:
        with open(mcore.LOCAL_PROFILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_local():
    data = {"layers": {str(k): {str(s): t for s, t in v.items()}
                       for k, v in PROFILE.items()}}
    try:
        os.makedirs(os.path.dirname(mcore.LOCAL_PROFILE), exist_ok=True)
        tmp = mcore.LOCAL_PROFILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, mcore.LOCAL_PROFILE)
    except OSError:
        pass


BACKUP_DIR = os.path.join(os.path.dirname(mcore.LOCAL_PROFILE), "backups")


def _slug(name):
    keep = "".join(c for c in (name or "").strip().lower()
                   if c.isalnum() or c in "-_ ")
    return keep.strip().replace(" ", "-")[:40]


def backup_list():
    out = []
    try:
        names = sorted(os.listdir(BACKUP_DIR))
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(BACKUP_DIR, fn)) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        layers = data.get("layers", {})
        out.append({
            "id": fn[:-5],
            "name": data.get("name", fn[:-5]),
            "saved": data.get("saved", ""),
            "counts": {str(l): len(layers.get(str(l), {})) for l in (1, 2, 3)},
        })
    return out


def backup_save(name, layers):
    slug = _slug(name)
    if not slug:
        raise ValueError("nombre vacío o inválido")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    data = {
        "name": (name or slug).strip(),
        "saved": time.strftime("%Y-%m-%d %H:%M"),
        "layers": {str(l): {str(s): str(t) for s, t in (layers.get(str(l)) or {}).items()
                            if str(t).strip()}
                   for l in (1, 2, 3)},
    }
    with open(os.path.join(BACKUP_DIR, slug + ".json"), "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    return slug


def backup_read(slug):
    with open(os.path.join(BACKUP_DIR, _slug(slug) + ".json")) as f:
        return json.load(f)


def backup_delete(slug):
    os.remove(os.path.join(BACKUP_DIR, _slug(slug) + ".json"))


def load_settings():
    try:
        with open(mcore.SETTINGS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(patch):
    s = load_settings()
    s.update(patch)
    try:
        os.makedirs(os.path.dirname(mcore.SETTINGS_PATH), exist_ok=True)
        with open(mcore.SETTINGS_PATH, "w") as f:
            json.dump(s, f, indent=1)
    except OSError:
        pass


def connect():
    err = P.connect()
    if err:
        DEVICE["keys"] = DEVICE["knobs"] = None
        return {"connected": False, "error": err}
    keys, knobs = P.identity()
    DEVICE["keys"], DEVICE["knobs"] = keys, knobs
    return {"connected": bool(P.h), "device": P.path, "keys": keys, "knobs": knobs}


def read_profile():
    global PROFILE
    profile = default_profile()
    if P.h and DEVICE["keys"] and DEVICE["knobs"]:
        for layer in (1, 2, 3):
            try:
                recs = P.read_layer(layer, DEVICE["keys"], DEVICE["knobs"])
            except Exception:
                continue
            for raw in recs:
                slot = raw[2] if len(raw) >= 3 else None
                if slot is None:
                    continue
                text = mcore.decode_record_macro(raw)
                if text:
                    profile[layer][slot] = text
    local = load_local()
    for layer in (1, 2, 3):
        for slot_s, text in local.get("layers", {}).get(str(layer), {}).items():
            try:
                slot = int(slot_s)
            except ValueError:
                continue
            if slot >= mcore.KNOB_BASE:
                profile[layer][slot] = text
    PROFILE = profile
    return profile


def profile_json():
    return {str(k): {str(s): t for s, t in v.items()} for k, v in PROFILE.items()}


def write_slots(slots, layer):
    if not P.h:
        raise RuntimeError("sin dispositivo")
    n = 0
    for slot in slots:
        P.write_binding(slot, layer, PROFILE[layer].get(slot, "borrar"))
        n += 1
    return n


# ---------------------------------------------------------------- HTTP
class Handler(SimpleHTTPRequestHandler):

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEBAPP, **kw)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        # la app nativa (WebKit) no debe cachear HTML/CSS viejos
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        if self.path.startswith("/api/backups"):
            return self._json({"backups": backup_list()})
        if self.path.startswith("/api/state"):
            with STATE_LOCK:
                if not P.h:
                    connect()
                st = {"connected": bool(P.h), "device": P.path,
                      "keys": DEVICE["keys"], "knobs": DEVICE["knobs"],
                      "profile": profile_json(),
                      "settings": {"theme": load_settings().get("theme", "light")}}
            return self._json(st)
        if self.path in ("/", ""):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        data = self._read_json()
        try:
            with STATE_LOCK:
                if self.path == "/api/connect":
                    connect()
                    read_profile()
                    return self._json({"connected": bool(P.h), "device": P.path,
                                       "keys": DEVICE["keys"], "knobs": DEVICE["knobs"],
                                       "profile": profile_json(),
                                       "settings": load_settings()})
                if self.path == "/api/write":
                    domain = data.get("domain", "keys")
                    layer = data.get("layer", 1)
                    layers = (1, 2, 3) if layer == "all" else [int(layer)]
                    done = 0
                    for lay in layers:
                        if domain in ("keys", "all"):
                            done += write_slots(KEYS_SLOTS, lay)
                        if domain in ("knobs", "all"):
                            done += write_slots(KNOBS_SLOTS, lay)
                    save_local()
                    return self._json({"ok": True, "written": done})
                if self.path == "/api/clear":
                    domain = data.get("domain", "keys")
                    layer = int(data.get("layer", 1))
                    slots = KEYS_SLOTS if domain == "keys" else KNOBS_SLOTS
                    for slot in slots:
                        PROFILE[layer].pop(slot, None)
                    save_local()
                    write_slots(slots, layer)
                    return self._json({"ok": True, "profile": profile_json()})
                if self.path == "/api/profile":
                    # el frontend empuja teclas/knobs editados (sin escribir al pad)
                    # merge: un update parcial nunca debe borrar el resto de la capa
                    layers = data.get("layers", {})
                    for lay_s, binds in layers.items():
                        try:
                            lay = int(lay_s)
                        except ValueError:
                            continue
                        PROFILE.setdefault(lay, {})
                        PROFILE[lay].update({int(s): str(t) for s, t in binds.items()})
                    save_local()
                    return self._json({"ok": True})
                if self.path == "/api/led":
                    layers = data.get("layers", [0])
                    mode = int(data.get("mode", 1))
                    colors = data.get("colors")
                    rgb = tuple(data.get("rgb", [255, 255, 255]))
                    if not P.h:
                        connect()
                    for lay in layers:
                        if colors and mode == 1:
                            if all(sum(c) < 48 for c in colors):
                                P.led_write(int(lay), 0, (0, 0, 0))
                            else:
                                P.led_colors(int(lay), mode, [tuple(c) for c in colors])
                        else:
                            P.led_write(int(lay), mode, rgb)
                    return self._json({"ok": True})
                if self.path == "/api/backups":
                    action = data.get("action", "save")
                    if action == "save":
                        layers = data.get("layers") or profile_json()
                        slug = backup_save(data.get("name", ""), layers)
                        return self._json({"ok": True, "id": slug,
                                           "backups": backup_list()})
                    if action == "load":
                        bk = backup_read(data.get("id", ""))
                        PROFILE.clear()
                        PROFILE.update(default_profile())
                        for lay_s, binds in (bk.get("layers") or {}).items():
                            try:
                                lay = int(lay_s)
                            except ValueError:
                                continue
                            PROFILE[lay] = {int(s): str(t) for s, t in binds.items()}
                        save_local()
                        return self._json({"ok": True, "profile": profile_json(),
                                           "name": bk.get("name", data.get("id"))})
                    if action == "delete":
                        backup_delete(data.get("id", ""))
                        return self._json({"ok": True, "backups": backup_list()})
                    return self._json({"error": "acción desconocida"}, 400)
                if self.path == "/api/settings":
                    save_settings(data)
                    return self._json({"ok": True, "settings": load_settings()})
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)


def main():
    connect()
    read_profile()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"MacroPad backend en http://127.0.0.1:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        P.close()


if __name__ == "__main__":
    main()
