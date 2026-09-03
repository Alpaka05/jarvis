"""Stimmen probehören.

    uv run python scripts/voice_test.py                      # aktuelle Stimme aus .env
    uv run python scripts/voice_test.py de-DE-KillianNeural  # bestimmte Edge-Stimme
    uv run python scripts/voice_test.py --list               # männliche deutsche/britische Stimmen auflisten
    uv run python scripts/voice_test.py --all                # alle Kandidaten nacheinander vorspielen
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import config  # noqa: E402
from core import platform_utils  # noqa: E402

SAMPLE = "Guten Abend. Ich bin Jarvis. Die Systeme sind online und ich stehe zu Ihrer Verfügung."
CANDIDATES = [
    "de-DE-ConradNeural",
    "de-DE-KillianNeural",
    "de-DE-FlorianMultilingualNeural",
    "de-AT-JonasNeural",
    "de-CH-JanNeural",
    "en-GB-RyanNeural",
    "en-GB-ThomasNeural",
]


def list_voices():
    import edge_tts

    voices = asyncio.run(edge_tts.list_voices())
    for v in voices:
        if v["Gender"] == "Male" and v["Locale"].startswith(("de-", "en-GB")):
            print(f"{v['ShortName']:<36} {v['Locale']}")


def play(voice: str):
    text = SAMPLE if voice.startswith("de-") else "Good evening. I am Jarvis. All systems are online and at your service."
    print(f"▶ {voice}  (rate {config.EDGE_RATE}, pitch {config.EDGE_PITCH})")
    path = platform_utils.synthesize_edge(text, voice)
    if not path:
        print("  Synthese fehlgeschlagen (Internet? Stimme korrekt geschrieben?)")
        return
    proc = platform_utils.play_audio_process(path)
    if proc:
        proc.wait()
    os.remove(path)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--list" in args:
        list_voices()
    elif "--all" in args:
        for v in CANDIDATES:
            play(v)
    else:
        play(args[0] if args else config.EDGE_VOICE)
