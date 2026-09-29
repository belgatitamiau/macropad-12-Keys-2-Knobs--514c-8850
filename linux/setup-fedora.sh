#!/usr/bin/env bash
# Fedora setup para el MacroPad 514c:8850 (CH57x).
# Instala la regla de udev (uaccess) para que WebHID/Chromium y padcheck.py
# puedan tocar el pad sin root, y verifica la enumeración.
set -e
cd "$(dirname "$0")"

echo "[1/4] Instalando regla de udev..."
sudo cp 60-ch57x-keypad.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger

echo
echo "[2/4] El pad en USB:"
if lsusb | grep -Ei "1189|514c"; then
  echo "  -> aparece en lsusb"
else
  echo "  -> NO aparece. Probá otro cable/port (C-to-C a veces no enciende en estos pads) y reconectá."
fi

echo
echo "[3/4] Nodos hidraw del pad:"
found=0
for d in /sys/class/hidraw/hidraw*; do
  ue=$(cat "$d/device/uevent" 2>/dev/null | grep HID_ID || true)
  if [[ "$ue" =~ 514C:00008850|0000514C.*8850 ]]; then
    echo "  /dev/$(basename "$d") -> $ue"
    found=1
  fi
done
if [[ $found -eq 0 ]]; then
  echo "  (sin nodos todavía — reconectá el pad después de la regla)"
fi

echo
echo "[4/4] Chequeo directo por hidraw:"
python3 padcheck.py || echo "  (avisá si el fallo es por permisos: reconectá el pad y volvé a correr)"
echo
echo "Listo. Para la UI del GitHub local:"
echo "  ./start-fedora.sh"
echo "  (necesitás Chromium: sudo dnf install chromium)"