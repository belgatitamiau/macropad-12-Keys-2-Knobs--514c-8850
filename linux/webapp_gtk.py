#!/usr/bin/env python3
"""MacroPad 514c:8850 — app de escritorio (GTK3 + WebKit2GTK).

Ventana nativa que carga el frontend servido por server.py en 127.0.0.1.
Sin navegador, sin barra de direcciones, sin pestañas.
El layout del frontend es fluido: la ventana ES la app (sin marco interno).
"""
import os
import sys
import urllib.request

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, WebKit2, GLib  # noqa: E402

PORT = int(os.environ.get("MACROPAD_PORT", "8799"))
URL = f"http://127.0.0.1:{PORT}/"

MIN_W, MIN_H = 560, 690
DEF_W, DEF_H = 860, 780


def server_up():
    try:
        with urllib.request.urlopen(URL + "api/state", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


class App(Gtk.Window):
    def __init__(self):
        super().__init__(title="MacroPad 514c:8850")
        self.set_wmclass("macropad-configurador", "MacroPad")
        self.set_icon_name("input-keyboard")
        self.set_resizable(True)
        self.set_default_size(DEF_W, DEF_H)
        self.set_size_request(MIN_W, MIN_H)

        self.web = WebKit2.WebView()
        self.web.set_hexpand(True)
        self.web.set_vexpand(True)
        self.web.set_size_request(1, 1)  # no impone tamaño mínimo al layout
        self.web.load_uri(URL)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(self.web, True, True, 0)
        self.add(box)

        self.connect("destroy", Gtk.main_quit)
        self.connect("key-press-event", self._on_key)
        # hooks de desarrollo: `touch /tmp/macropad-snapshot` => PNG de la vista
        GLib.timeout_add(600, self._watch_snapshot)
        GLib.timeout_add(300, self._watch_eval)

    def _watch_snapshot(self):
        flag = "/tmp/macropad-snapshot"
        if os.path.exists(flag):
            try:
                os.remove(flag)
            except OSError:
                pass
            self._snapshot()
        return True

    def _on_key(self, _widget, event):
        # F5 / Ctrl+R recargan; F11 pantalla completa
        from gi.repository import Gdk
        if event.keyval == Gdk.KEY_F5 or (
            event.keyval == Gdk.KEY_r and event.state & Gdk.ModifierType.CONTROL_MASK
        ):
            self.web.reload()
            return True
        if event.keyval == Gdk.KEY_F11:
            if self.get_window() and self.get_window().get_state() & Gdk.WindowState.FULLSCREEN:
                self.unfullscreen()
            else:
                self.fullscreen()
            return True
        if event.keyval == Gdk.KEY_F12:
            self._snapshot()
            return True
        return False

    def _watch_eval(self):
        """Ejecuta el JS de /tmp/macropad-eval y deja el resultado en .out."""
        if os.path.exists("/tmp/macropad-eval"):
            try:
                with open("/tmp/macropad-eval") as f:
                    js = f.read()
                os.remove("/tmp/macropad-eval")
            except OSError:
                js = ""
            if js.strip():
                self.web.run_javascript(js, None, self._on_eval, None)
        return True

    def _on_eval(self, webview, result, _data):
        try:
            value = webview.run_javascript_finish(result)
            out = value.get_js_value().to_string() if value else ""
        except Exception as exc:
            out = "ERROR: %s" % exc
        with open("/tmp/macropad-eval.out", "w") as f:
            f.write(out)

    def _snapshot(self, path="/tmp/opencode/app-shot.png"):
        self.web.get_snapshot(WebKit2.SnapshotRegion.VISIBLE,
                              WebKit2.SnapshotOptions.NONE,
                              None, self._on_snapshot, path)

    def _on_snapshot(self, webview, result, path):
        try:
            surface = webview.get_snapshot_finish(result)
            surface.write_to_png(path)
            print("snapshot:", path, flush=True)
        except Exception as exc:  # pragma: no cover
            print("snapshot failed:", exc, flush=True)


def main():
    if not server_up():
        print("Backend no disponible: iniciá server.py primero", file=sys.stderr)
        return 1
    app = App()
    app.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
