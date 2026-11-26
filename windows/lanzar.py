"""MacroPad 514c:8850 — launch the new UI on Windows.

Starts the local backend (server.py) if needed, then opens the frontend in a
browser app window (Edge/Chrome --app: no address bar, no tabs).
Standard library only.
"""
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "http://127.0.0.1:8799/"
PY = sys.executable or "python"


def backend_up():
    try:
        with urllib.request.urlopen(URL + "api/state", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def browser_path():
    pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Local"))
    return [
        os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
    ]


def main():
    if not backend_up():
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(
            [PY, os.path.join(HERE, "server.py")],
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        for _ in range(40):
            if backend_up():
                break
            time.sleep(0.3)
        else:
            print("No se pudo iniciar el backend local")
            return 1

    for exe in browser_path():
        if os.path.isfile(exe):
            subprocess.Popen([exe, "--app=" + URL, "--window-size=900,820",
                              "--no-first-run", "--no-default-browser-check"])
            return 0
    webbrowser.open(URL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
