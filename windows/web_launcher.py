"""MacroPad 514c:8850 — Configurador (launcher del web tool local).

Serves the local copy of the CH57x web configurator (the GitHub UI) on
http://127.0.0.1 and opens it in a Chromium browser. WebHID only works in
Chromium (Chrome/Edge/Opera/Thorium), so Firefox/Safari cannot be used.

Keeps running until you close this window.
"""

import os
import socket
import subprocess
import sys
import threading
import tkinter as tk
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = 8787

BASE_DIR = (os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, "web")


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)


def pick_browser():
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Thorium\Application\thorium.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("MacroPad 514c:8850 — Configurador")
        self.geometry("480x230")
        self.resizable(False, False)
        self.server = None
        self.url = None

        f = tk.Frame(self, padx=18, pady=16)
        f.pack(fill="both", expand=True)
        tk.Label(f, text="MacroPad 514c:8850", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        self.lbl = tk.Label(f, text="", justify="left", fg="#333",
                            font=("Segoe UI", 9), wraplength=440)
        self.lbl.pack(anchor="w", pady=(6, 12))

        b = tk.Frame(f)
        b.pack(anchor="w")
        tk.Button(b, text="Abrir configuración", command=self.open_local,
                  width=20).pack(side="left", padx=(0, 6))
        tk.Button(b, text="Abrir online (respaldo)", command=self.open_online,
                  width=22).pack(side="left", padx=6)
        tk.Button(b, text="Salir", command=self._close, width=8).pack(side="left", padx=6)

        self.protocol("WM_DELETE_WINDOW", self._close)
        self._start_server()

    def _start_server(self):
        if not os.path.isfile(os.path.join(WEB_DIR, "index.html")):
            self.lbl.configure(text=f"No encontré la carpeta web en:\n{WEB_DIR}")
            return
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", PORT), Quiet)
        except OSError as e:
            self.lbl.configure(text=f"No pude levantar el servidor local: {e}")
            return
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{PORT}/"
        self.lbl.configure(
            text=f"Servidor local: {self.url}\n"
                 "Conectá el pad (botón Connect keypad) y usá 'Alternative B' "
                 "(es el formato por defecto de tu 514c:8850). LEDs incluidos.")
        self.open_local()

    def _open(self, url):
        exe = pick_browser()
        if exe:
            subprocess.Popen([exe, url])
        else:
            os.startfile(url)

    def open_local(self):
        if self.url:
            self._open(self.url)

    def open_online(self):
        self._open("https://jvspier.github.io/macropad-rebind")

    def _close(self):
        try:
            if self.server:
                self.server.shutdown()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()