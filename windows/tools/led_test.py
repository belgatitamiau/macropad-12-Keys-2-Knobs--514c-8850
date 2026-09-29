"""LED protocol tester for the 514c:8850 pad.

Tries LED record formats and reports which one changes the backlight.
Read-only-ish: only writes LED records. Never sends 0xEF / 0xFC.

Usage:
  python led_test.py [candidate]
    candidate 1 (default): spokino format  (FB preamble + FE B0 layer0 mode + 17 RGB triplets)
    candidate 2            jvspier format  (FE B0 layer1 08 ... (colour<<4)|mode, no preamble)
  Extra args: mode r g b layer
"""

import sys
import time

import probe as p
import dump as d

OUT = 65
IN = 65


def send(h, payload):
    msg = bytes(payload) + b"\x00" * (OUT - len(payload))
    _, err = p.sync_transfer(h, True, msg)
    time.sleep(0.12)
    return err


def led_spokino(h, layer0, mode, rgb):
    """FB preamble + FE B0 layer(0-based) mode + 17 RGB triplets (key1 first)."""
    send(h, [0x03, 0xFB, 0xFB, 0xFB])
    pkt = [0x03, 0xFE, 0xB0, layer0 & 0xFF, mode & 0xFF]
    for _ in range(17):
        pkt += [rgb[0], rgb[1], rgb[2]]
    send(h, pkt)


def led_jvspier(h, layer1, mode, colour):
    """One-byte code (colour<<4)|mode, header 03 FE B0 layer(1-based) 08 ..."""
    code = (colour << 4) | mode
    send(h, [0x03, 0xFE, 0xB0, layer1, 0x08, 0, 0, 0, 0, 0, 0x01, 0x00, code])


def main():
    cand = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    args = [int(x) for x in sys.argv[2:]]
    mode = args[0] if len(args) > 0 else 1      # 1 = static
    r, g, b = (args[1], args[2], args[3]) if len(args) > 3 else (255, 0, 0)
    layer = args[4] if len(args) > 4 else 0     # 0-based

    path, caps = d.find_target()
    if not path:
        print("device not found")
        return 1
    h = p.open_path(path)
    keys, knobs = d.identity(h)
    print(f"identity: keys={keys} knobs={knobs}")

    if cand == 1:
        print(f"[candidate 1 - spokino] mode={mode} rgb=({r},{g},{b}) layer0={layer}")
        led_spokino(h, layer, mode, (r, g, b))
    else:
        # colour 1-7 map: 1=red; pick red=1 for jvspier scheme
        colour = args[5] if len(args) > 5 else 1
        print(f"[candidate 2 - jvspier] mode={mode} colour={colour} layer1={layer + 1}")
        led_jvspier(h, layer + 1, mode, colour)

    print("sent. check the pad LEDs now.")
    p.kernel32.CloseHandle(h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())