#!/bin/bash
# Jarvis mit Oberfläche starten (macOS): Dashboard-Fenster statt Eingabe im Terminal.
# Das Terminal-Fenster bleibt im Hintergrund offen – macOS bindet Mikrofon- und Bildschirmzugriff
# an das Programm, aus dem Jarvis gestartet wurde.
exec "$(dirname "$0")/jarvis.command" --ui "$@"
