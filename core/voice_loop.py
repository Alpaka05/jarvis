"""Sprachmodus: dauerhaft lauschen, auf „Hey Jarvis“ reagieren, antworten, weiterlauschen.

Ablauf:
    Wake-Word erkannt → kurzer Bestätigungston → Aufnahme bis Sprechpause →
    Erkennung → Agent → Sprachausgabe (unterbrechbar durch Reinreden) →
    kurzes Nachfrage-Fenster ohne Wake-Word → zurück zum Lauschen
"""
from __future__ import annotations

import logging
import re
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from config import config
from core import orb, platform_utils
from core.agent import UNTRUSTED_NOTE, JarvisAgent
from core.mic import MicError, MicStream
from core.voice import VoiceEngine
from core.voice_input import VoiceInputListener, rms
from core.wakeword import FRAME_SAMPLES, SAMPLE_RATE, WakeWordDetector

log = logging.getLogger(__name__)
MAX_MIC_FAILURES = 5  # so oft hintereinander darf das Mikrofon ausfallen, dann endet der Sprachmodus


def _tone(freq: float, duration: float, volume: float) -> np.ndarray:
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    tone = np.sin(2 * np.pi * freq * t) * volume
    env = np.minimum(1.0, np.linspace(0, 12, len(tone)))  # kurzer Einschwinger
    env *= np.linspace(1, 0, len(tone)) ** 1.5  # Ausklang
    return (tone * env).astype(np.float32)


def play_beep(freq: float = 880.0, duration: float = 0.12, volume: float = 0.4):
    """Einzelner Ton über das Windows-Standardgerät (Soundmapper) bzw. die Systemausgabe."""
    play_samples(_tone(freq, duration, volume))


def play_chime(kind: str = "wake"):
    """Bestätigungs-Chimes: wake = aufsteigend (ich höre), done = kurz tief (Aufnahme beendet),
    fail = abfallend (nicht verstanden)."""
    if kind == "wake":
        samples = np.concatenate([_tone(660, 0.09, 0.45), _tone(990, 0.14, 0.45)])
    elif kind == "done":
        samples = _tone(520, 0.08, 0.3)
    else:
        samples = np.concatenate([_tone(660, 0.09, 0.4), _tone(440, 0.16, 0.4)])
    play_samples(samples)


def play_samples(samples: np.ndarray, sample_rate: int = SAMPLE_RATE):
    try:
        platform_utils.play_audio(samples, sample_rate, device=platform_utils.default_output_device())
    except Exception:
        try:
            platform_utils.play_audio(samples, sample_rate)
        except Exception:
            pass


def _trim_silence(pcm: np.ndarray, threshold: int = 400, pad: int = 800) -> np.ndarray:
    """Schneidet führende/abschließende Stille ab (int16), lässt etwas Puffer stehen."""
    loud = np.where(np.abs(pcm.astype(np.int32)) > threshold)[0]
    if loud.size == 0:
        return pcm
    start = max(0, loud[0] - pad)
    end = min(len(pcm), loud[-1] + pad)
    return pcm[start:end]


class AckVoice:
    """Kurze gesprochene Bestätigung („Ja?“) in Jarvis' Stimme.

    Wird einmal per Edge-TTS erzeugt, als mp3 gecacht und beim Start in den Speicher
    dekodiert, damit die Wiedergabe ohne Verzögerung startet.
    """

    def __init__(self, phrase: str):
        self.phrase = phrase.strip()
        self.path: Optional[str] = None
        self.pcm: Optional[np.ndarray] = None
        self.sample_rate = SAMPLE_RATE
        if not self.phrase:
            return
        self.path = platform_utils.synthesize_cached(self.phrase, "ack")
        if self.path:
            self._decode()

    def _decode(self):
        try:
            import miniaudio

            dec = miniaudio.decode_file(self.path, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1)
            pcm = np.frombuffer(bytes(dec.samples), dtype=np.int16)
            self.pcm = _trim_silence(pcm)
            self.sample_rate = dec.sample_rate
        except Exception:
            self.pcm = None  # Fallback: externer Player

    def play(self, wait: bool = True):
        if self.pcm is not None:
            play_samples(self.pcm, self.sample_rate)
            return
        if not self.path:
            return
        proc = platform_utils.play_audio_process(self.path)
        if proc is not None and wait:
            proc.wait()


class VoiceLoop:
    def __init__(
        self,
        agent: JarvisAgent,
        voice: VoiceEngine,
        console: Optional[Console] = None,
        listener: Optional[VoiceInputListener] = None,
        detector: Optional[WakeWordDetector] = None,
        follow_up_seconds: float = 6.0,
        barge_in_threshold: float = 0.06,
        on_wake: Optional[Callable[[], None]] = None,
    ):
        self.agent = agent
        self.voice = voice
        self.console = console or Console()
        self.listener = listener or VoiceInputListener(
            language=config.LANGUAGE,
            sample_rate=SAMPLE_RATE,
            vad_threshold=config.VAD_THRESHOLD,
            silence_limit=config.SILENCE_LIMIT_SECONDS,
        )
        self.detector = detector or WakeWordDetector(config.WAKE_WORD_MODEL, config.WAKE_WORD_THRESHOLD)
        self.follow_up_seconds = follow_up_seconds
        self.barge_in_threshold = barge_in_threshold
        self.on_wake = on_wake
        self.running = False
        self._stream = None  # offenes Mikrofon, für Rückfragen mitten in einer Anfrage
        self.ack_style = config.ACK_STYLE
        self.ack_voice = AckVoice(config.ACK_PHRASE) if self.ack_style in ("voice", "both") and config.TTS_ENABLED else None

    def _acknowledge(self):
        """Bestätigung nach dem Wake-Word: Chime und/oder gesprochenes „Ja?“."""
        if self.ack_style in ("chime", "both") or self.ack_voice is None or not self.ack_voice.path:
            play_chime("wake")
        if self.ack_voice is not None:
            self.ack_voice.play(wait=True)

    # ── Hauptschleife ────────────────────────────────────────────────────────

    def run(self):
        self.running = True
        self.console.print(
            Panel(
                f"Sprachmodus aktiv. Sag [bold cyan]„Hey Jarvis“[/bold cyan] und stell deine Frage.\n"
                f"[dim]Modell {self.detector.model_name}, Schwelle {self.detector.threshold:.2f}. "
                f"Strg+C beendet den Sprachmodus.[/dim]",
                title="[bold green]🎤 Lausche[/bold green]",
                border_style="green",
            )
        )
        self.agent.voice_mode = True
        # Rückfragen (Mail senden, Türschloss …) per Sprache statt über die Tastatur
        keyboard_confirm = getattr(self.agent, "confirm", None)
        self.agent.confirm = self._confirm_by_voice
        orb.state("idle")
        failures = 0
        try:
            while self.running:
                # Bei geänderten Audiogeräten oder einem ausgefallenen Mikrofon schließen und neu
                # öffnen – dabei liest PortAudio die Geräteliste frisch ein
                opened = time.monotonic()
                try:
                    with platform_utils.open_audio_stream(
                        lambda: MicStream(samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES)
                    ) as stream:
                        self._listen(stream)
                    failures = 0
                except (MicError, sd.PortAudioError) as e:
                    if time.monotonic() - opened > 30:
                        failures = 0  # lief eine Weile: kein Dauerproblem
                    failures += 1
                    log.warning("Mikrofon-Problem (%d/%d): %s", failures, MAX_MIC_FAILURES, e)
                    self.voice.stop()
                    orb.state("idle")
                    if failures >= MAX_MIC_FAILURES:
                        self.console.print(
                            f"[bold red]Mikrofon nicht verfügbar[/bold red] ({escape(str(e))}) – Sprachmodus beendet."
                        )
                        break
                    self.console.print(f"[yellow]Mikrofon-Problem ({escape(str(e))}) – öffne es neu …[/yellow]")
                    time.sleep(min(failures, 3))
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            self.voice.stop()
            self.agent.confirm = keyboard_confirm
            self._stream = None
            self.agent.voice_mode = False
            orb.state("idle")

    def stop(self):
        self.running = False

    DEVICE_CHECK_FRAMES = 25  # ~2 s bei 80 ms pro Frame

    def _listen(self, stream):
        """Lauscht auf das Wake-Word, bis der Modus endet oder sich die Audiogeräte ändern."""
        self.detector.reset()
        self._stream = stream
        frames = 0
        while self.running:
            frame, _ = stream.read(FRAME_SAMPLES)
            if self.detector.triggered(frame):
                self._interaction(stream)
                orb.state("idle")
                self.detector.reset()
                frames = self.DEVICE_CHECK_FRAMES  # nach dem Gespräch gleich prüfen
            frames += 1
            if frames >= self.DEVICE_CHECK_FRAMES:
                frames = 0
                if platform_utils.audio_devices_changed():
                    return

    # ── Eine Interaktion (mit Nachfrage-Fenster) ─────────────────────────────

    def _interaction(self, stream):
        if self.on_wake:
            self.on_wake()
        orb.state("wake")
        self._acknowledge()
        # Eigene Bestätigung nicht als Sprache aufnehmen
        stream.read(int(SAMPLE_RATE * 0.15))
        orb.state("listening")
        self.console.print("[bold yellow]🎤 Ich höre ...[/bold yellow]")
        start_timeout = 6.0

        while self.running:
            recording = self.listener.record_from_stream(stream, start_timeout=start_timeout)
            if recording is None:
                self.console.print("[dim]Nichts gehört, lausche weiter.[/dim]")
                return
            play_chime("done")
            orb.state("thinking")
            text = self.listener.recognize(recording)
            if not text:
                self.console.print("[dim]Nicht verstanden.[/dim]")
                orb.error("Nicht verstanden")
                play_chime("fail")
                return
            self.console.print(f"[bold cyan]Du:[/bold cyan] {escape(text)}")
            orb.transcript("user", text)
            if self._is_text_mode_request(text):  # „Wechsel in den Chatmodus“ → Sprachmodus verlassen
                self.console.print("[dim]Okay, zurück in den Chatmodus.[/dim]")
                self.running = False
                return
            if self._is_stop_phrase(text):  # „Danke“, „Das war's“ … → Gespräch sofort beenden
                self.console.print("[dim]Okay, bis später. Lausche wieder auf „Hey Jarvis“.[/dim]")
                return

            # Jarvis beginnt zu sprechen, sobald der erste Satz der Antwort fertig ist
            speech = self.voice.open_stream(listen_for_interrupt=False)
            try:
                with self.console.status("[bold green]Denke nach...[/bold green]", spinner="dots"):
                    response = self.agent.process_query(text, on_text=speech.feed)
            finally:
                speech.close()
            self.console.print(Panel(escape(response), title="[bold green]Jarvis[/bold green]", border_style="green"))
            if getattr(self.agent, "last_usage", None) and self.agent.last_usage.calls:
                self.console.print(f"  [dim]{self.agent.usage_summary(self.agent.last_usage)}[/dim]")
            orb.transcript("assistant", response)

            interrupted = self._watch_for_barge_in(stream)
            if interrupted == "wake":
                # „Hey Jarvis“ mitten in der Antwort: wie ein neuer Aufruf behandeln
                orb.state("wake")
                play_chime("wake")
                stream.read(int(SAMPLE_RATE * 0.15))
                orb.state("listening")
                self.console.print("[bold yellow]🎤 Ich höre ...[/bold yellow]")
                start_timeout = 6.0
                continue
            # Nachfrage-Fenster: direkt weitersprechen, ohne erneutes Wake-Word
            start_timeout = 1.5 if interrupted else self.follow_up_seconds
            if start_timeout <= 0:
                return
            orb.state("listening")
            self.console.print("[dim]… noch etwas? (ohne Wake-Word)[/dim]")

    # ── Rückfrage per Sprache ────────────────────────────────────────────────

    YES_WORDS = {
        "ja", "jawohl", "jap", "jo", "yes", "okay", "ok", "genau", "bestätigt", "bestätige", "klar", "sicher",
        "gerne", "gern", "mach", "machs", "los", "richtig", "korrekt",
    }
    NO_WORDS = {"nein", "nö", "nee", "ne", "no", "nicht", "stopp", "stop", "abbrechen", "halt", "lieber", "falsch"}

    @classmethod
    def _is_yes(cls, text: str) -> bool:
        """Nur ein klares Ja zählt; sobald ein Nein-Wort vorkommt („ja, aber nicht jetzt“), gilt Nein."""
        words = "".join(ch for ch in (text or "").lower() if ch.isalpha() or ch == " ").split()
        return not any(w in cls.NO_WORDS for w in words) and any(w in cls.YES_WORDS for w in words)

    def _confirm_by_voice(self, prompt: str) -> bool:
        """Liest die Rückfrage vor und wartet auf „ja“ oder „nein“. Keine oder unklare Antwort = Nein."""
        stream = self._stream
        lines = prompt.strip().splitlines()
        summary = (lines[0] if lines else "Diese Aktion ausführen").rstrip(" .:?")
        self.console.print(
            Panel(escape(prompt), title="[bold yellow]Bestätigung nötig – sag „ja“ oder „nein“[/bold yellow]", border_style="yellow")
        )
        if stream is None:
            return False
        question = f"{summary}. Soll ich das machen?"
        if UNTRUSTED_NOTE in prompt:
            question += " Achtung, das könnte aus einer Webseite oder Mail stammen."
        self.voice.speak(question, listen_for_interrupt=False, block=True)
        # Was sich während Denken und Vorlesen im Puffer gesammelt hat (inkl. Echo), verwerfen
        try:
            if hasattr(stream, "flush"):
                stream.flush()
            stream.read(int(SAMPLE_RATE * 0.25))  # Nachhall
        except MicError:
            return False
        orb.state("listening")
        recording = self.listener.record_from_stream(stream, start_timeout=6.0)
        orb.state("thinking")
        if recording is None:
            self.console.print("[dim]Keine Antwort – nicht ausgeführt.[/dim]")
            return False
        answer = self.listener.recognize(recording)
        self.console.print(f"[bold cyan]Du:[/bold cyan] {escape(answer) or '(nicht verstanden)'}")
        confirmed = self._is_yes(answer)
        self.console.print("[green]Bestätigt.[/green]" if confirmed else "[dim]Nicht ausgeführt.[/dim]")
        return confirmed

    STOP_PHRASES = {
        "stopp", "stop", "stopp stopp", "halt", "danke", "danke das reicht", "das reicht", "reicht",
        "okay danke", "ok danke", "danke jarvis", "jarvis stopp", "jarvis stop", "abbrechen", "sei ruhig",
        "ruhe", "schon gut", "passt", "alles gut", "nichts", "nein danke", "vergiss es",
        "das wäre alles", "das war alles", "das ist alles", "wäre alles", "war alles",
    }

    # „Danke, das war's“ & Co.: Ein Satz beendet das Gespräch, wenn er nur aus diesen Wörtern
    # besteht und mindestens ein klares Abschluss-Signal enthält. Sobald etwas Inhaltliches dabei
    # ist („Nein, mach das Licht aus“), geht er normal an den Agenten.
    CLOSING_SIGNALS = {
        "danke", "dankeschön", "dankesehr", "dank", "thanks", "wars", "reicht", "passt", "tschüss",
        "tschau", "ciao", "bye", "stopp", "stop", "halt", "fertig", "nein", "nö", "nee", "ne", "nichts",
        "abbrechen", "erledigt",
    }
    CLOSING_FILLER = {
        "jarvis", "das", "war", "es", "wäre", "alles", "schon", "gut", "sehr", "schön", "vielen", "lieben",
        "herzlichen", "dir", "ok", "okay", "klar", "super", "perfekt", "top", "prima", "toll",
        "cool", "genau", "dann", "bis", "später", "erstmal", "erst", "mal", "mehr", "sonst", "weiter",
        "ja", "danke", "nochmal", "auch", "so", "und", "ich", "brauche", "brauch", "wir", "sind",
    }

    @classmethod
    def _is_stop_phrase(cls, text: str) -> bool:
        norm = "".join(ch for ch in text.lower() if ch.isalpha() or ch == " ").strip()
        if norm in cls.STOP_PHRASES:
            return True
        words = norm.split()
        return (
            bool(words)
            and len(words) <= 8
            and any(w in cls.CLOSING_SIGNALS for w in words)
            and all(w in cls.CLOSING_SIGNALS or w in cls.CLOSING_FILLER for w in words)
        )

    # Zurück in die Texteingabe: Modus-Wörter (auch „Chat Modus“/„Chat-Modus“) oder klare Wendungen.
    # Bewusst eng gefasst, damit z.B. „Schreib eine Mail an Max“ eine normale Anfrage bleibt.
    TEXT_MODE_WORDS = ("chatmodus", "textmodus", "tippmodus", "schreibmodus", "texteingabe", "chateingabe")
    TEXT_MODE_PATTERN = re.compile(
        r"\b(?:"
        r"(?:zurück|wechsel\w*|geh\w*|schalt\w*) (?:zum|in den|zu dem|auf den) chat"
        r"|ich (?:will|möchte|würde|mag) (?:(?:lieber|jetzt|wieder|gerne?|selbst|selber) )*(?:tippen|schreiben)"
        r"|lass mich (?:(?:lieber|jetzt|wieder|selbst|selber) )*(?:tippen|schreiben)"
        r"|sprachmodus (?:beenden|verlassen|stoppen|aus|ausschalten)"
        r"|(?:beende|verlass|stopp|stoppe) (?:den )?sprachmodus"
        r")\b"
    )

    @classmethod
    def _is_text_mode_request(cls, text: str) -> bool:
        norm = " ".join("".join(ch for ch in text.lower() if ch.isalpha() or ch == " ").split())
        if any(w in norm.replace(" ", "") for w in cls.TEXT_MODE_WORDS):
            return True
        return bool(cls.TEXT_MODE_PATTERN.search(norm))

    def _watch_for_barge_in(self, stream) -> Optional[str]:
        """Hört zu, solange die Antwort gesprochen wird. Abbruch durch „Hey Jarvis“ (Rückgabe 'wake')
        oder durch anhaltendes, lautes Dazwischenreden (Rückgabe 'speech'). None = zu Ende gesprochen."""
        # Während Jarvis nachdachte, hat sich Mikrofon-Audio gestaut (samt Echo der ersten Sätze) –
        # nur auf das reagieren, was ab jetzt kommt
        if hasattr(stream, "flush"):
            stream.flush()
        self.detector.reset()
        speech_frames = 0
        needed = 6  # ~0.5 s zusammenhängende Sprache oberhalb der Lautstärke-Schwelle
        while self.voice.is_speaking():
            frame, _ = stream.read(FRAME_SAMPLES)
            # 1) Wake-Word unterbricht immer – robust gegen das Echo der eigenen Ausgabe
            if self.detector.triggered(frame):
                self.voice.stop()
                self.console.print("[dim]Unterbrochen durch „Hey Jarvis“.[/dim]")
                return "wake"
            # 2) Lautes, anhaltendes Reinreden (0 = deaktiviert)
            if self.barge_in_threshold > 0 and rms(frame) > self.barge_in_threshold:
                prob = self.listener.speech_probability(frame)
                speech_frames = speech_frames + 1 if (prob is None or prob >= 0.5) else 0
                if speech_frames >= needed:
                    self.voice.stop()
                    self.console.print("[dim]Unterbrochen.[/dim]")
                    return "speech"
            else:
                speech_frames = 0
        # Nachhall der eigenen Ausgabe verwerfen
        stream.read(int(SAMPLE_RATE * 0.25))
        self.detector.reset()
        return None
