#!/usr/bin/env python3
"""MacroPad 514c:8850 — chequeo y test de LEDs en Linux (hidraw, sin deps).

Uso:
  ./padcheck.py            -> identidad + estado
  ./padcheck.py led        -> ciclo corto: rojo fijo -> apagado
  ./padcheck.py red        -> rojo fijo (modo 1)
  ./padcheck.py off        -> apagar LEDs
  ./padcheck.py rainbow    -> arcoíris (modo 4)

Solo escribe registros de LED (formato spokino). Nunca manda 0xEF ni 0xFC.
Si da "Permission denied", corré setup-fedora.sh y reconectá el pad.
"""

import os
import glob
import select
import time
import sys

TARGET = (0x514C, 0x8850)
OUT_LEN = 65
IN_LEN = 65
MODE = {"off": 0, "fijo": 1, "reacciona": 2, "ripple": 3, "arcoiris": 4, "rainbow": 4}


def hid_nodes():
    """[(/dev/hidrawN, vid, pid)] para el dispositivo."""
    out = []
    for path in glob.glob("/sys/class/hidraw/hidraw*"):
        try:
            with open(path + "/device/uevent") as f:
                ue = f.read()
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


def has_vendor_collection(node):
    """True si el report descriptor declara usage page 0xFF00 usage 0x01."""
    base = os.path.basename(node)
    try:
        with open(f"/sys/class/hidraw/{base}/device/report_descriptor", "rb") as f:
            data = f.read()
    except OSError:
        return False
    usage_page = None
    usage = None
    i = 0
    while i < len(data):
        b = data[i]
        i += 1
        size = b & 3
        btype = (b >> 2) & 3
        btag = (b >> 4) & 15
        dlen = 4 if size == 3 else size
        d = data[i:i + dlen]
        i += dlen
        if btype == 0 and btag == 8:  # collection (main)
            if d and d[0] == 1 and usage_page == 0xFF00 and usage == 0x0001:
                return True
        elif btype == 1 and btag == 4:  # usage page (global)
            usage_page = d[0] if dlen == 1 else int.from_bytes(d[:2], "little")
        elif btype == 2 and btag == 0:  # usage (local)
            usage = d[0] if dlen == 1 else int.from_bytes(d[:2], "little")
    return False


def find_node():
    for node, vid, pid in hid_nodes():
        if (vid, pid) == TARGET and has_vendor_collection(node):
            return node
    return None


def send(fd, payload):
    buf = bytes(payload) + b"\x00" * (OUT_LEN - len(payload))
    os.write(fd, buf)


def read_reply(fd, timeout=1.0):
    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        return None
    return os.read(fd, IN_LEN)


def identity(fd):
    send(fd, [0x03, 0xFB, 0xFB, 0xFB])
    for _ in range(4):
        data = read_reply(fd, 1.0)
        if data and len(data) >= 4 and data[1] == 0xFB:
            return data[2], data[3]
    return None, None


def led(fd, layer0, mode, rgb):
    send(fd, [0x03, 0xFB, 0xFB, 0xFB])
    pkt = [0x03, 0xFE, 0xB0, layer0 & 0xFF, mode & 0xFF]
    for _ in range(17):
        pkt += [rgb[0] & 0xFF, rgb[1] & 0xFF, rgb[2] & 0xFF]
    send(fd, pkt)


def main():
    node = find_node()
    if not node:
        print("No encontré el pad 514c:8850 (colección vendor 0xFF00/0x01).")
        print("Chequeá el cable y que la regla udev esté aplicada:")
        print("  sudo cp 60-ch57x-keypad.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules")
        return 2

    print("Pad:", node)
    try:
        fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
    except PermissionError:
        print("Permission denied en", node)
        print("Aplicá la regla udev y reconectá el pad (o corré con sudo).")
        return 3

    keys, knobs = identity(fd)
    print(f"Identidad: {keys} teclas, {knobs} knobs")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "led":
        for rgb in ((255, 0, 0), (0, 128, 255), (128, 0, 255), (0, 0, 0)):
            for layer0 in (0, 1, 2):
                led(fd, layer0, 1, rgb)
            time.sleep(0.4)
    elif cmd == "off":
        for layer0 in (0, 1, 2):
            led(fd, layer0, 0, (0, 0, 0))
        print("LEDs apagados")
    elif cmd in MODE:
        rgb = (255, 255, 255)
        for layer0 in (0, 1, 2):
            led(fd, layer0, MODE[cmd], rgb)
        print(f"LED modo {cmd} aplicado")
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())