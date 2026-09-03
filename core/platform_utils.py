"""Betriebssystem-Abstraktion: Sprachausgabe, Benachrichtigungen, URLs öffnen.

Alles, was vorher nur über macOS-Kommandos (`say`, `osascript`, `open`) lief,
wird hier für Windows, macOS und Linux bereitgestellt.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import webbrowser
from typing import Optional

from config import config, IS_WINDOWS, IS_MAC, IS_LINUX

# ── Windows helpers ─────────────────────────────────────────────────────────

_PS_TTS_SCRIPT = r"""
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$text = [Console]::In.ReadToEnd()
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $env:JARVIS_VOICE
$culture = $env:JARVIS_LANG
$selected = $false
if ($voice) { try { $s.SelectVoice($voice); $selected = $true } catch {} }
if (-not $selected -and $culture) {
    try { $s.SelectVoiceByHints(0, 0, 0, [System.Globalization.CultureInfo]$culture) } catch {}
}
$s.Speak($text)
"""

_PS_TOAST_SCRIPT = r"""
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$j = [Console]::In.ReadToEnd() | ConvertFrom-Json
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName("text")
$texts.Item(0).AppendChild($template.CreateTextNode($j.title)) | Out-Null
$texts.Item(1).AppendChild($template.CreateTextNode($j.message)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
"""


_PS_PLAY_SCRIPT = r"""
Add-Type -AssemblyName PresentationCore
$p = New-Object System.Windows.Media.MediaPlayer
$p.Open([Uri]$env:JARVIS_AUDIO)
$p.Play()
$deadline = (Get-Date).AddSeconds(15)
while (-not $p.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 30 }
if ($p.NaturalDuration.HasTimeSpan) {
    $ms = [int]$p.NaturalDuration.TimeSpan.TotalMilliseconds
    $end = (Get-Date).AddMilliseconds($ms + 150)
    while ((Get-Date) -lt $end) { Start-Sleep -Milliseconds 50 }
}
$p.Stop(); $p.Close()
"""


def _powershell_cmd(script: str) -> list[str]:
    exe = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]


def _no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


# ── Public API ──────────────────────────────────────────────────────────────

def default_voice() -> str:
    """Systemabhängige Standardstimme, wenn VOICE_NAME nicht gesetzt ist."""
    if config.VOICE_NAME:
        return config.VOICE_NAME
    if IS_MAC:
        return "Anna"
    return ""  # Windows/Linux: Auswahl über Sprache (LANGUAGE)


def tts_available() -> bool:
    if IS_MAC:
        return shutil.which("say") is not None
    if IS_WINDOWS:
        return True
    return any(shutil.which(x) for x in ("spd-say", "espeak-ng", "espeak"))


# ── Audio-Wiedergabe & Edge-TTS ─────────────────────────────────────────────

_output_device_cache: dict = {}


def default_output_device() -> Optional[int]:
    """Ausgabegerät für sounddevice, das dem Windows-Standardgerät folgt.

    PortAudio wählt unter Windows gern ein beliebiges MME-Gerät; der
    „Microsoft Soundmapper“ leitet dagegen immer auf das aktuelle Standardgerät.
    """
    if "device" in _output_device_cache:
        return _output_device_cache["device"]
    device: Optional[int] = None
    if IS_WINDOWS:
        try:
            import sounddevice as sd

            for idx, d in enumerate(sd.query_devices()):
                name = d["name"].lower().replace(" ", "")
                if d["max_output_channels"] > 0 and "soundmapper" in name and "output" in name:
                    device = idx
                    break
        except Exception:
            device = None
    _output_device_cache["device"] = device
    return device


def synthesize_cached(text: str, cache_name: str) -> Optional[str]:
    """Erzeugt eine Edge-TTS-Datei einmalig und legt sie unter data/ ab (z.B. Bestätigungsphrasen)."""
    import hashlib

    key = hashlib.sha1(f"{config.EDGE_VOICE}|{config.EDGE_RATE}|{config.EDGE_PITCH}|{text}".encode("utf-8")).hexdigest()[:10]
    path = config.DATA_DIR / f"{cache_name}_{key}.mp3"
    if path.exists() and path.stat().st_size > 0:
        return str(path)
    tmp = synthesize_edge(text)
    if not tmp:
        return None
    try:
        shutil.move(tmp, path)
        return str(path)
    except Exception:
        return tmp

def play_audio_process(path: str) -> Optional[subprocess.Popen]:
    """Spielt eine Audiodatei (mp3) als eigenen, abbrechbaren Prozess ab."""
    try:
        if IS_MAC:
            return subprocess.Popen(["afplay", path])
        if IS_WINDOWS:
            env = dict(os.environ, JARVIS_AUDIO=path)
            return subprocess.Popen(
                _powershell_cmd(_PS_PLAY_SCRIPT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=_no_window_flags(),
            )
        if IS_LINUX:
            for exe, args in (("mpv", ["--no-video", "--really-quiet"]), ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]), ("mpg123", ["-q"])):
                if shutil.which(exe):
                    return subprocess.Popen([exe, *args, path])
    except Exception:
        return None
    return None


def synthesize_edge(text: str, voice: Optional[str] = None) -> Optional[str]:
    """Erzeugt mit Microsoft-Edge-TTS eine mp3-Datei und gibt den Pfad zurück (None bei Fehler)."""
    try:
        import tempfile

        import edge_tts

        voice = voice or config.EDGE_VOICE
        fd, path = tempfile.mkstemp(prefix="jarvis_", suffix=".mp3")
        os.close(fd)
        communicate = edge_tts.Communicate(text, voice, rate=config.EDGE_RATE, pitch=config.EDGE_PITCH)
        communicate.save_sync(path)
        if os.path.getsize(path) == 0:
            os.remove(path)
            return None
        return path
    except Exception:
        return None


def cleanup_process(proc: subprocess.Popen):
    """Löscht temporäre Dateien, die an einen Sprachprozess gebunden sind."""
    path = getattr(proc, "_jarvis_tmp", None)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def speak_process(text: str, voice: Optional[str] = None) -> Optional[subprocess.Popen]:
    """Startet die Sprachausgabe als eigenen Prozess (unterbrechbar via .terminate()).

    Nutzt je nach TTS_ENGINE die Edge-Neural-Stimmen (online) und fällt bei Fehlern
    automatisch auf die Systemstimme zurück. Gibt None zurück, wenn keine Ausgabe möglich ist.
    """
    if config.TTS_ENGINE == "edge":
        path = synthesize_edge(text)
        if path:
            proc = play_audio_process(path)
            if proc is not None:
                proc._jarvis_tmp = path  # type: ignore[attr-defined]
                return proc
            os.remove(path)
    return speak_system_process(text, voice)


def speak_system_process(text: str, voice: Optional[str] = None) -> Optional[subprocess.Popen]:
    """Sprachausgabe über die Betriebssystem-Stimme (Windows SAPI, macOS say, Linux espeak)."""
    voice = voice if voice is not None else default_voice()
    try:
        if IS_MAC:
            cmd = ["say"] + (["-v", voice] if voice else []) + [text]
            return subprocess.Popen(cmd)

        if IS_WINDOWS:
            env = dict(os.environ, JARVIS_VOICE=voice or "", JARVIS_LANG=config.LANGUAGE)
            proc = subprocess.Popen(
                _powershell_cmd(_PS_TTS_SCRIPT),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=_no_window_flags(),
            )
            assert proc.stdin is not None
            proc.stdin.write(text.encode("utf-8"))
            proc.stdin.close()
            return proc

        if IS_LINUX:
            lang = config.LANGUAGE[:2]
            candidates = (
                ("spd-say", ["-w", "-l", lang]),
                ("espeak-ng", ["-v", lang]),
                ("espeak", ["-v", lang]),
            )
            for exe, args in candidates:
                if shutil.which(exe):
                    return subprocess.Popen([exe, *args, text])
    except Exception:
        return None
    return None


def speak_blocking(text: str, voice: Optional[str] = None) -> bool:
    proc = speak_process(text, voice)
    if proc is None:
        return False
    proc.wait()
    cleanup_process(proc)
    return proc.returncode == 0


def voice_label() -> str:
    """Beschreibung der aktiven Stimme für den Statusbildschirm."""
    if config.TTS_ENGINE == "edge":
        return f"{config.EDGE_VOICE} (Edge Neural, Fallback: Systemstimme)"
    return default_voice() or "Systemstimme"


def notify(title: str, message: str) -> bool:
    """Zeigt eine Desktop-Benachrichtigung. Gibt True bei Erfolg zurück."""
    try:
        if IS_MAC:
            safe_title = title.replace('"', "'")
            safe_msg = message.replace('"', "'")
            script = f'display notification "{safe_msg}" with title "{safe_title}"'
            subprocess.run(["osascript", "-e", script], check=False, timeout=10)
            return True

        if IS_WINDOWS:
            payload = json.dumps({"title": title, "message": message}).encode("utf-8")
            res = subprocess.run(
                _powershell_cmd(_PS_TOAST_SCRIPT),
                input=payload,
                capture_output=True,
                timeout=15,
                creationflags=_no_window_flags(),
            )
            return res.returncode == 0

        if IS_LINUX and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], check=False, timeout=10)
            return True
    except Exception:
        return False
    return False


def open_url(url: str) -> bool:
    """Öffnet eine URL im Standardbrowser (plattformunabhängig)."""
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:
        return False


def platform_label() -> str:
    return config.platform_name
