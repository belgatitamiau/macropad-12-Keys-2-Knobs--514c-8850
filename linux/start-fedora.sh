#!/usr/bin/env bash
# Sirve la UI local del configurador (la misma del GitHub, con LEDs) y la abre
# en Chromium. Ctrl+C para terminar (apaga el server local).
set -e
cd "$(dirname "$0")"

PORT="${PORT:-8787}"
URL="http://127.0.0.1:${PORT}/"

echo "Sirviendo la UI en $URL"
python3 -m http.server "$PORT" --bind 127.0.0.1 --directory web &
SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
sleep 1

BROWSER=""
for b in chromium chromium-browser google-chrome microsoft-edge brave-browser; do
  if command -v "$b" >/dev/null 2>&1; then BROWSER="$b"; break; fi
done

if [ -n "$BROWSER" ]; then
  echo "Abriendo $BROWSER"
  "$BROWSER" "$URL" 2>/dev/null &
else
  echo "No encontré Chromium — abrí vos: $URL"
  echo "WebHID NO funciona en Firefox. Instalá Chromium: sudo dnf install chromium"
fi

echo "Presioná Ctrl+C para cerrar el server local."
wait "$SRV"