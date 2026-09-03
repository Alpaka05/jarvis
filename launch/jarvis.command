#!/bin/bash
# Doppelklick oder Stream Deck "Öffnen" startet Jarvis im Terminal (macOS)
cd "$(dirname "$0")/.."
uv run python main.py
