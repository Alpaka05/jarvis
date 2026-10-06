#!/bin/bash
# Doppelklick oder Stream Deck "Öffnen" startet Jarvis im Terminal (macOS)
cd "$(dirname "$0")/.."
# Per Doppelklick oder Stream Deck fehlt oft der PATH aus der Shell-Konfiguration – uv dann
# an den üblichen Orten suchen
UV="$(command -v uv || true)"
for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
  [ -n "$UV" ] && break
  [ -x "$candidate" ] && UV="$candidate"
done
if [ -z "$UV" ]; then
  echo "uv nicht gefunden – Installation: https://docs.astral.sh/uv/"
else
  "$UV" run python main.py
fi

# Nach "exit" in einer normalen Shell im Jarvis-Ordner weitermachen statt "[Prozess beendet]"
exec "$SHELL" -l
