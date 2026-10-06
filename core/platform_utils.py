"""Betriebssystem-Abstraktion: Sprachausgabe, Benachrichtigungen, URLs öffnen.

Alles, was vorher nur über macOS-Kommandos (`say`, `osascript`, `open`) lief,
wird hier für Windows, macOS und Linux bereitgestellt.
"""
from __future__ import annotations

import contextlib
import faulthandler
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from typing import Optional

from config import IS_LINUX, IS_MAC, IS_WINDOWS, config

log = logging.getLogger(__name__)

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

# PortAudio (PaMacCore) schreibt Warnungen wie „||PaMacCore (AUHAL)|| … '!obj'“ direkt auf
# die Dateideskriptoren (per printf auf stdout), vorbei an Python. Ursache ist meist eine veraltete Geräteliste, z.B. nach
# einem Bluetooth-Wechsel: PortAudio liest die Geräte nur bei der Initialisierung ein.
_audio_lock = threading.RLock()
_open_streams = 0


_stderr_lock = threading.RLock()
_native_stderr_silenced = False


def silence_native_output(log_path: Optional[str] = None) -> bool:
    """Leitet die Dateideskriptoren 1 und 2 dauerhaft in `log_path` um (ohne Pfad: /dev/null);
    Pythons sys.stdout und sys.stderr schreiben weiter ins Terminal.

    C-Bibliotheken schreiben direkt auf die Deskriptoren – PortAudio (PaMacCore) etwa per printf
    auf stdout, onnxruntime auf stderr – und landen so in der Logdatei statt im Terminal. Dort
    bleiben auch Absturzmeldungen („Fatal Python error“, Segfault) nachlesbar. Python-Ausgaben,
    rich und Tracebacks bleiben sichtbar; faulthandler schreibt Python-Stacks bei einem Absturz
    ins Terminal. Kindprozesse erben die umgeleiteten Deskriptoren. Einmal pro Prozess, idempotent.
    """
    global _native_stderr_silenced
    with _stderr_lock:
        if _native_stderr_silenced:
            return True
        try:
            replaced = []
            target = log_path or os.devnull
            for name in ("stdout", "stderr"):
                stream = getattr(sys, name)
                stream.flush()
                fd = stream.fileno()
                real = os.dup(fd)
                sink = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                os.dup2(sink, fd)
                os.close(sink)
                encoding = getattr(stream, "encoding", None) or "utf-8"
                replaced.append((name, os.fdopen(real, "w", encoding=encoding, errors="replace", buffering=1)))
            for name, stream in replaced:
                setattr(sys, name, stream)
            faulthandler.enable(file=sys.stderr)  # Python-Stack bei Segfault/Abbruch ins Terminal
        except Exception:
            log.warning("Native Ausgaben konnten nicht umgeleitet werden", exc_info=True)
            return False
        _native_stderr_silenced = True
        return True


@contextlib.contextmanager
def quiet_stderr():
    """Schaltet stderr auf Dateideskriptor-Ebene stumm (für C-Bibliotheken wie PortAudio).

    Serialisiert über ein Lock, damit sich Fenster aus verschiedenen Threads nicht überlappen
    (sonst könnte ein Thread stderr wiederherstellen, während ein anderer noch drin ist).
    """
    with _stderr_lock:
        if _native_stderr_silenced:
            yield
            return
        try:
            sys.stderr.flush()
            fd = sys.stderr.fileno()
        except Exception:
            yield
            return
        saved = os.dup(fd)
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, fd)
            yield
        finally:
            try:
                sys.stderr.flush()
            except Exception:
                pass
            os.dup2(saved, fd)
            os.close(saved)
            os.close(devnull)


def refresh_audio_devices() -> None:
    """Initialisiert PortAudio neu, damit die Geräteliste aktuell ist.

    Nur sicher, wenn gerade kein Stream offen ist; open_audio_stream() kümmert sich darum.
    """
    global _device_signature
    try:
        import sounddevice as sd

        with quiet_stderr():
            sd._terminate()
            sd._initialize()
    except Exception:
        log.warning("PortAudio-Geräteliste konnte nicht aufgefrischt werden", exc_info=True)
    _output_device_cache.clear()
    _device_signature = audio_device_signature()


# ── Geräteänderungen erkennen (macOS) ───────────────────────────────────────
# PortAudio kennt nur die Geräte vom letzten Initialisieren. Läuft der Sprachmodus lange, bleibt
# das Mikrofon dauerhaft offen, und die Liste veraltet, sobald z.B. ein Monitor mit Lautsprechern
# schlafen geht. Ausgaben landen dann auf nicht mehr vorhandenen Geräten („'!obj'“, -10851,
# kratziger Ton). CoreAudio direkt zu fragen kostet nur Mikrosekunden.

_device_signature = None
_coreaudio = None


def _coreaudio_lib():
    global _coreaudio
    if _coreaudio is None:
        import ctypes

        class Address(ctypes.Structure):
            _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]

        lib = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        lib.AudioObjectGetPropertyDataSize.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(Address), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        lib.AudioObjectGetPropertyData.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(Address), ctypes.c_uint32, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        _coreaudio = (ctypes, lib, Address)
    return _coreaudio


def audio_device_signature() -> Optional[tuple]:
    """(Geräte-IDs, Standard-Eingang, Standard-Ausgang) laut CoreAudio; None außerhalb von macOS."""
    if not IS_MAC:
        return None
    try:
        ctypes, lib, Address = _coreaudio_lib()
        fourcc = lambda code: int.from_bytes(code.encode("ascii"), "big")  # noqa: E731
        system, glob = 1, fourcc("glob")  # kAudioObjectSystemObject, kAudioObjectPropertyScopeGlobal

        def read(selector: str) -> list:
            addr = Address(fourcc(selector), glob, 0)
            size = ctypes.c_uint32(0)
            if lib.AudioObjectGetPropertyDataSize(system, ctypes.byref(addr), 0, None, ctypes.byref(size)):
                return []
            buf = (ctypes.c_uint32 * (size.value // 4))()
            if lib.AudioObjectGetPropertyData(system, ctypes.byref(addr), 0, None, ctypes.byref(size), buf):
                return []
            return list(buf)

        return tuple(read("dev#")), tuple(read("dIn ")), tuple(read("dOut"))
    except Exception:
        return None


def audio_devices_changed() -> bool:
    """True, wenn sich Geräte oder Standardgeräte seit dem letzten Auffrischen geändert haben."""
    global _device_signature
    current = audio_device_signature()
    if current is None:
        return False
    if _device_signature is None:
        _device_signature = current
        return False
    return current != _device_signature


@contextlib.contextmanager
def open_audio_stream(factory):
    """Öffnet einen sounddevice-Stream ohne PortAudio-Geschwätz auf stderr.

    `factory` erzeugt den Stream (z.B. `lambda: sd.InputStream(...)`). Ist kein anderer Stream
    offen, wird die Geräteliste vorher aufgefrischt; scheitert das Öffnen, wird nach einem
    Auffrischen ein zweites Mal versucht.
    """
    global _open_streams
    with _audio_lock:
        if _open_streams == 0:
            refresh_audio_devices()
        try:
            with quiet_stderr():
                stream = factory().__enter__()  # startet den Stream (wie `with sd.InputStream(...)`)
        except Exception:
            if _open_streams != 0:
                raise
            log.info("Audio-Stream ließ sich nicht öffnen – Geräteliste auffrischen und erneut versuchen", exc_info=True)
            refresh_audio_devices()
            with quiet_stderr():
                stream = factory().__enter__()
        _open_streams += 1
    try:
        yield stream
    finally:
        # Erst schließen, dann zählen: Sähe ein anderer Thread vorher 0, würde er PortAudio neu
        # initialisieren (refresh_audio_devices), während dieser Stream noch stoppt
        try:
            with quiet_stderr():
                stream.__exit__(None, None, None)  # stop + close
        except Exception:
            log.warning("Audio-Stream ließ sich nicht sauber schließen", exc_info=True)
        finally:
            with _audio_lock:
                _open_streams -= 1


def play_audio(samples, sample_rate: int, device=None) -> None:
    """Blockierende Wiedergabe eines Arrays über einen eigenen Stream (Ersatz für sd.play/sd.wait)."""
    import numpy as np
    import sounddevice as sd

    data = np.asarray(samples)
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    if data.dtype.kind == "f":
        data = data.astype(np.float32)
    rate = output_sample_rate(device)
    if rate and rate != sample_rate:
        data, sample_rate = resample(data, sample_rate, rate), rate
    dtype = str(data.dtype)
    with open_audio_stream(
        lambda: sd.OutputStream(samplerate=sample_rate, channels=data.shape[1], dtype=dtype, device=device)
    ) as out:
        out.write(np.ascontiguousarray(data))


def output_sample_rate(device=None) -> Optional[int]:
    """Native Abtastrate des Ausgabegeräts unter macOS, sonst None (Rate egal).

    Teilen sich Mikrofon und Ausgabe ein Gerät (z.B. USB-Mikrofon mit Kopfhörerausgang) und
    wechseln aufeinanderfolgende Ausgabe-Streams die Rate, bricht CoreAudio den laufenden
    Mikrofon-Stream ab („PaMacCore (AUHAL) … err='-50'“) – danach kommen keine Daten mehr.
    Deshalb unter macOS immer in der nativen Rate des Geräts abspielen.
    """
    if not IS_MAC:
        return None
    key = f"rate:{device}"
    with _audio_lock:  # nicht parallel zu refresh_audio_devices() abfragen
        if key not in _output_device_cache:
            try:
                import sounddevice as sd

                rate = int(sd.query_devices(device, "output")["default_samplerate"])
            except Exception:
                log.debug("Abtastrate von Ausgabegerät %r nicht abfragbar", device, exc_info=True)
                return None  # nicht merken: beim nächsten Mal erneut versuchen
            _output_device_cache[key] = rate or None
        return _output_device_cache[key]


def resample(samples, src_rate: int, dst_rate: int):
    """Lineare Umrechnung der Abtastrate (für kurze Töne und Sprache ausreichend), Datentyp bleibt."""
    import numpy as np

    data = np.asarray(samples)
    if src_rate == dst_rate or len(data) == 0:
        return data
    n = max(1, int(round(len(data) * dst_rate / src_rate)))
    pos = np.linspace(0, len(data) - 1, n)
    idx = np.arange(len(data))
    flat = data.reshape(len(data), -1).astype(np.float32)
    out = np.stack([np.interp(pos, idx, flat[:, c]) for c in range(flat.shape[1])], axis=1)
    if data.dtype.kind == "i":
        info = np.iinfo(data.dtype)
        out = np.clip(np.round(out), info.min, info.max)
    return out.astype(data.dtype).reshape((n,) + data.shape[1:])


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
            # "--": Text, der mit "-" beginnt (z.B. Aufzählungen), sonst als Option gelesen
            cmd = ["say"] + (["-v", voice] if voice else []) + ["--", text]
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
                    return subprocess.Popen([exe, *args, "--", text])
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
            # Titel und Text als argv übergeben, nie in den Skripttext einsetzen: sonst kann
            # LLM-gesteuerter Text (z.B. mit "\" am Ende) aus dem String ausbrechen und AppleScript
            # bzw. per "do shell script" beliebige Befehle ausführen.
            subprocess.run(
                [
                    "osascript",
                    "-e", "on run argv",
                    "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                    "-e", "end run",
                    "--", title, message,
                ],
                check=False,
                timeout=10,
            )
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
