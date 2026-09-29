# MacroPad 514c:8850 — Configurator

All-in-one configurator for the cheap Aliexpress **"mini macro keyboard"**: **12 keys + 2 clickable rotary knobs, 3 layers, per-key RGB backlight**, which enumerates on USB as **`514c:8850`** (the "CH57x" family convention used for these pads).

Works on **Windows** (GUI app + local web UI) and **Linux/Fedora** (udev rule + hidraw CLI + web UI). No drivers required for the core features.

---

## Why this project exists

- The vendor's bundled software is an old Chinese Qt app that **cannot configure this model**: its internal device table only lists vendor `0x1189` devices, and its "Read device" button blocks forever (it opens the wrong HID interface and waits).
- The well-known community tools (`kriomant/ch57x-keyboard-tool`, `jvspier/macropad-rebind`) were written around the `1189:8840/8842` family.
- This unit speaks the **"Alternative B"** dialect (`0xFD` write marker) and ships under vendor `0x514C`, which most tools won't even list.

Everything here was verified on a real unit: reads, writes, LED records, and knob slot numbering.

---

## Features

### Windows configurator app (`windows/macropad_configurator.py`)
- **Reads the current key bindings from the pad on startup** (the pad's own read-back, `0xFA`).
- Writes **keys and knobs**: single keys, modifier chords (`ctrl-alt-shift-s`), multi-key sequences (`a,b,c`), media (`volumeup`, `play`, `mute`, ...).
- **Quick-assign bar**: type a macro once, then tap any key or knob action to assign it — works for many controls in a row.
- **LED control**: per-key RGB (17-triplet "spokino" record), modes 0–5, auto-applies changes live.
- Dark theme, resizable layout, knob rendering.

### Web UI wrapper (`windows/web_launcher.py`)
Serves a **local copy of the GitHub-style web configurator** (patched `web/`) and opens it in Chromium via WebHID. The patch adds **LED support for the Alternative B dialect** (spokino format), so the same UI handles keys, knobs, layers and backlight.

### Gamepad mode (`windows/gamepad.py`) — experimental
Maps pad keys to a **virtual Xbox 360 controller** via the ViGEm bus driver (already installed on many gaming rigs). Needs the pad keys bound to F13–F24; requires `vgamepad`.
[More details in the file header.]

### Linux / Fedora support (`linux/`)
- `60-ch57x-keypad.rules` — systemd `uaccess` rule so WebHID/Chromium and hidraw can talk to the pad without root.
- `padcheck.py` — dependency-free hidraw CLI: identity + LED test.
- `setup-fedora.sh` / `start-fedora.sh` — one-shot setup and local web UI launcher.

---

## Hardware facts (verified on a real 12+2 unit)

| | |
|---|---|
| USB IDs | `0x514c:0x8850` (also in the `1189:8840/8842/8850` family) |
| Layout | 12 keys + 2 clickable knobs, 3 layers |
| Vendor HID collection | usage page `0xFF00`, usage `0x01`, **65-byte** reports (writing 64 is silently ignored) |
| Dialect | "Alternative B" (`0xFD` write marker) works; the firmware also answers the `0xFE` family writes and the `0xFA` read |
| Key slots | 1–12 (read-back via `0xFA` works) |
| Knob slots | **base 16**, verified: knob1 = 16/17/18 (ccw/press/cw), knob2 = 19/20/21. **No read-back for knobs.** |
| LED record | `03 FB FB FB` preamble, then `03 FE B0 <layer 0-based> <mode>` + 17 RGB triplets; modes `0` off, `1` static, `2` reactive, `3` ripple, `4` rainbow, `5` rainbow #2 |
| Modifiers | inline entries `0xF1`–`0xF8` (ctrl/shift/alt/win and right variants) |

> Firmware varies between units with the same VID/PID. This project probes the identity and reports what it finds; the tools above were tested on a unit that answers `0xFA` read-back and accepts both write dialects.

---

## Build & run

### Windows — run from source
```bat
python windows/macropad_configurator.py
```

### Windows — build the exe
```bat
pip install pyinstaller
pyinstaller --onefile --noconsole --name MacroPadLED windows\macropad_configurator.py
```

### Linux / Fedora
```bash
sudo cp linux/60-ch57x-keypad.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# replug the pad, then either run the CLI or the web UI:
python3 linux/padcheck.py led
sudo dnf install chromium && ./linux/start-fedora.sh
```

---

## ⚠️ Safety

- **Never send `0xEF`** — it puts the firmware in its bootloader. There is **no published firmware image** for this board, so once you're in there, it's a brick.
- `0xFC FC <keys> <knobs>` re-declares the hardware variant. Wrong pairs make keys stop lighting/responding. Recovery for a 12+2 unit is `03 FC FC 0C 02` + a LED record + a commit.
- C-to-C cables often don't power these pads (they lack USB-C CC pull-downs) — use a USB-A to C cable.
- WebHID only works in Chromium browsers (Chrome, Edge, Opera, Thorium), not Firefox.

---

## Repository layout

```
web/                        Patched macropad-rebind UI (MIT), LEDs enabled for Alternative B
windows/
  macropad_configurator.py  Main Windows app: keys + knobs + LEDs + quick-assign
  web_launcher.py           Serves web/ at 127.0.0.1 and opens Chromium
  gamepad.py                Experimental: pad keys -> virtual Xbox 360 (ViGEm)
  tools/                    Driver-free HID scripts: probe, dump, setkey, keylog, led_test
linux/
  60-ch57x-keypad.rules     udev uaccess rule
  padcheck.py               hidraw identity + LED CLI
  setup-fedora.sh / start-fedora.sh
docs/                       Protocol notes
```

## Credits

- Web UI: [jvspier/macropad-rebind](https://github.com/jvspier/macropad-rebind) (MIT) — shipped here with a small LED patch.
- Protocol research: spokino's hardware-verified notes for the `514c:8850` (key/media/LED records, knob slots), [jvspier's PROTOCOL.md](https://github.com/jvspier/macropad-rebind/blob/main/docs/PROTOCOL.md), [kriomant/ch57x-keyboard-tool](https://github.com/kriomant/ch57x-keyboard-tool).
- udev rule: taken from macropad-rebind.

## License

MIT — see [LICENSE](LICENSE).