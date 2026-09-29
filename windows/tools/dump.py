"""Read-only config dump for the 514c:8850 macropad (ch57x-1 dialect).

Uses the 0xFA read command to back up all three layers.
STRICTLY READ-ONLY: never sends 0xFC (variant) or 0xEF (bootloader), never writes.
"""

import json
import os
import datetime

import probe as p

OUT_LEN = 65
IN_LEN = 65

KEYCODES = {
    0x04: "a", 0x05: "b", 0x06: "c", 0x07: "d", 0x08: "e", 0x09: "f",
    0x0A: "g", 0x0B: "h", 0x0C: "i", 0x0D: "j", 0x0E: "k", 0x0F: "l",
    0x10: "m", 0x11: "n", 0x12: "o", 0x13: "p", 0x14: "q", 0x15: "r",
    0x16: "s", 0x17: "t", 0x18: "u", 0x19: "v", 0x1A: "w", 0x1B: "x",
    0x1C: "y", 0x1D: "z", 0x1E: "1", 0x1F: "2", 0x20: "3", 0x21: "4",
    0x22: "5", 0x23: "6", 0x24: "7", 0x25: "8", 0x26: "9", 0x27: "0",
    0x28: "enter", 0x29: "esc", 0x2A: "backspace", 0x2B: "tab", 0x2C: "space",
    0x2D: "minus", 0x2E: "equal", 0x2F: "[", 0x30: "]", 0x31: "\\",
    0x33: ";", 0x34: "'", 0x35: "`", 0x36: ",", 0x37: ".", 0x38: "/",
    0x3A: "f1", 0x3B: "f2", 0x3C: "f3", 0x3D: "f4", 0x3E: "f5", 0x3F: "f6",
    0x40: "f7", 0x41: "f8", 0x42: "f9", 0x43: "f10", 0x44: "f11", 0x45: "f12",
    0x4F: "right", 0x50: "left", 0x51: "down", 0x52: "up",
    0x4A: "home", 0x4B: "pageup", 0x4D: "end", 0x4E: "pagedown",
    0x4C: "delete", 0x49: "insert",
}
MODBITS = [(0x01, "ctrl"), (0x02, "shift"), (0x04, "alt"), (0x08, "win"),
           (0x10, "rctrl"), (0x20, "rshift"), (0x40, "ralt"), (0x80, "rwin")]
MEDIA = {0xB5: "next", 0xB6: "previous", 0xB7: "stop", 0xCD: "play",
         0xE2: "mute", 0xE9: "volumeup", 0xEA: "volumedown"}
MOUSE_ACTION = {1: "click", 3: "wheel", 4: "wheel?", 5: "move/drag"}
MQ = {1: "click", 3: "wheel", 5: "move/drag"}


def find_target():
    for path in p.enumerate_hid_paths():
        h = p.open_path(path)
        if not h or h == p.INVALID_HANDLE_VALUE:
            continue
        try:
            attrs, caps = p.get_info(h)
            if (attrs and attrs.VendorID == p.TARGET_VID
                    and attrs.ProductID == p.TARGET_PID and caps
                    and caps.UsagePage == 0xFF00 and caps.Usage == 1):
                return path, caps
        finally:
            p.kernel32.CloseHandle(h)
    return None, None


def identity(h):
    msg = bytes([0x03, 0xFB, 0xFB, 0xFB]) + b"\x00" * (OUT_LEN - 4)
    p.sync_transfer(h, True, msg)
    for _ in range(4):
        data, err = p.sync_transfer(h, False, bytes(IN_LEN), timeout_ms=1200)
        if err:
            break
        if data and data[1] == 0xFB:
            return data[2], data[3]
    return None, None


def gather(h, keys, knobs, layer):
    msg = bytes([0x03, 0xFA, keys, knobs, layer]) + b"\x00" * (OUT_LEN - 5)
    p.sync_transfer(h, True, msg)
    recs = []
    empties = 0
    for _ in range(80):
        data, err = p.sync_transfer(h, False, bytes(IN_LEN), timeout_ms=600)
        if err or not data:
            empties += 1
            if empties >= 3:
                break
            continue
        if len(data) >= 2 and data[1] == 0xFA:
            empties = 0
            recs.append(data)
        else:
            break
    return recs


def decode(raw):
    d = {}
    d["raw"] = raw[:16].hex()
    d["marker"] = raw[1]
    d["slot"] = raw[2]
    d["layer"] = raw[3]
    d["type"] = raw[4]
    d["count"] = raw[10]
    t = raw[4]
    if t == 1:
        steps = []
        n = raw[10]
        for i in range(n):
            base = 11 + i * 2
            if base + 1 >= len(raw):
                break
            mod, code = raw[base], raw[base + 1]
            mods = [name for bit, name in MODBITS if mod & bit]
            steps.append({"modifiers": mods, "code": hex(code),
                          "key": KEYCODES.get(code, f"<{code}>")})
        d["kind"] = "keyboard"
        d["steps"] = steps
    elif t == 2:
        usage = raw[11] | (raw[12] << 8)
        d["kind"] = "media"
        d["usage"] = hex(usage)
        d["action"] = MEDIA.get(usage & 0xFF, f"<{usage}>")
    elif t == 3:
        d["kind"] = "mouse"
        d["mouse_action"] = raw[10]
        d["modifier"] = raw[11]
        d["buttons"] = raw[12]
        d["dx"] = raw[13]
        d["dy"] = raw[14]
        d["wheel"] = raw[15]
    elif t == 5:
        ms = raw[5] | (raw[6] << 8)
        d["kind"] = "delay"
        d["ms"] = ms
    elif t == 8:
        d["kind"] = "led"
        d["flag"] = raw[10]
    else:
        d["kind"] = f"type{t}"
    return d


def main():
    path, caps = find_target()
    if not path:
        print("device not found")
        return 1
    print("device:", path)
    h = p.open_path(path)
    keys, knobs = identity(h)
    print(f"identity: keys={keys} knobs={knobs}\n")

    backup = {"vid": f"0x{p.TARGET_VID:04x}", "pid": f"0x{p.TARGET_PID:04x}",
              "keys": keys, "knobs": knobs, "dialect": "ch57x-1 (0xFE)",
              "layers": {}}

    for layer in (1, 2, 3):
        recs = gather(h, keys, knobs, layer)
        print(f"--- layer {layer}: {len(recs)} records ---")
        by_slot = {}
        for raw in recs:
            d = decode(raw)
            by_slot.setdefault(d["slot"], []).append(d)
            desc = d.get("kind")
            if d.get("kind") == "keyboard":
                seq = ",".join(
                    ("-".join(s["modifiers"] + [s["key"]]) if s["modifiers"]
                     else s["key"]) for s in d.get("steps", []))
                desc = f"keyboard: {seq}"
            elif d.get("kind") == "media":
                desc = f"media: {d.get('action')}"
            elif d.get("kind") == "mouse":
                desc = (f"mouse: act={d.get('mouse_action')} "
                        f"btn={d.get('buttons')} dx={d.get('dx')} dy={d.get('dy')} "
                        f"wheel={d.get('wheel')}")
            elif d.get("kind") == "delay":
                desc = f"delay: {d.get('ms')}ms"
            elif d.get("kind") == "led":
                desc = f"led: flag={d.get('flag')}"
            print(f"  slot {d['slot']:>3} (0x{d['slot']:02x}) layer {d['layer']} "
                  f"type {d['type']} -> {desc}")
            print(f"        raw: {d['raw']}")
        backup["layers"][str(layer)] = {str(k): v for k, v in by_slot.items()}
        print()

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "backup-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(backup, f, indent=2)
    print("backup written:", out)
    p.kernel32.CloseHandle(h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
