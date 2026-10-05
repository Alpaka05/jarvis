"""Spracheingabe: Aufnahme bis Sprechpause und Erkennung (Google Speech Recognition).

Sprache wird bevorzugt mit dem Silero-VAD (neuronale Sprachaktivitätserkennung, kommt mit
openWakeWord) erkannt. Damit zählen Atempausen, Tastaturgeräusche oder der Nachhall der
eigenen Ausgabe nicht als Sprechen bzw. Satzende. Ohne VAD wird auf eine Lautstärke-Schwelle
mit Hysterese zurückgefallen.

Kann einen eigenen Mikrofon-Stream öffnen (Konsolenbefehl 'v') oder einen bereits
geöffneten Stream mitbenutzen (Sprachmodus mit Wake-Word).
"""
from __future__ import annotations

import io
import wave
from typing import Optional

import numpy as np
import sounddevice as sd
import speech_recognition as sr
from rich.console import Console

from core import orb, platform_utils

console = Console()

VAD_FRAME = 480  # 30 ms @ 16 kHz, vom Silero-VAD erwartet
STT_TIMEOUT_SECONDS = 8  # Google-Spracherkennung: Netz-Timeout pro Anfrage


def rms(chunk: np.ndarray) -> float:
    data = chunk.astype(np.float32)
    return float(np.sqrt(np.mean(data**2)) / 32768.0) if data.size else 0.0


class VoiceInputListener:
    def __init__(
        self,
        language: str = "de-DE",
        sample_rate: int = 16000,
        use_vad: bool = True,
        vad_threshold: float = 0.5,
        silence_limit: float = 1.4,
    ):
        self.recognizer = sr.Recognizer()
        # Ohne Timeout wartet urlopen bei hängendem Netz ewig und der Sprachmodus friert ein
        self.recognizer.operation_timeout = STT_TIMEOUT_SECONDS
        self.language = language
        self.sample_rate = sample_rate
        self.chunk_size = VAD_FRAME * 2  # 60 ms – ganzzahliges Vielfaches des VAD-Frames
        self.vad_threshold = vad_threshold
        self.silence_limit = silence_limit
        self.vad = None
        if use_vad:
            try:
                from openwakeword.vad import VAD

                self.vad = VAD()
            except Exception:
                self.vad = None

    # ── Sprachaktivität ──────────────────────────────────────────────────────

    def _reset_vad(self):
        if self.vad is None:
            return
        try:
            self.vad.reset_states()
        except Exception:
            try:
                self.vad._h[:] = 0
                self.vad._c[:] = 0
            except Exception:
                pass

    def speech_probability(self, chunk: np.ndarray) -> Optional[float]:
        """Wahrscheinlichkeit 0–1, dass der Block Sprache enthält (None ohne VAD)."""
        if self.vad is None:
            return None
        mono = chunk[:, 0] if chunk.ndim > 1 else chunk
        usable = (len(mono) // VAD_FRAME) * VAD_FRAME
        if usable == 0:
            return None
        try:
            return float(self.vad.predict(mono[:usable].astype(np.int16), frame_size=VAD_FRAME))
        except Exception:
            return None

    def measure_ambient(self, stream, seconds: float = 0.3) -> float:
        chunks = []
        for _ in range(max(1, int(seconds * self.sample_rate / self.chunk_size))):
            c, _ = stream.read(self.chunk_size)
            chunks.append(c)
        return rms(np.concatenate(chunks, axis=0))

    def speech_threshold(self, ambient: float) -> float:
        return max(0.012, ambient * 3.0)

    # ── Aufnahme ─────────────────────────────────────────────────────────────

    def record_from_stream(
        self,
        stream,
        max_duration: float = 30.0,
        silence_limit: Optional[float] = None,
        start_timeout: Optional[float] = None,
        threshold: Optional[float] = None,
    ) -> Optional[np.ndarray]:
        """Nimmt aus einem offenen Stream auf, bis nach dem Sprechen eine Pause folgt.

        Args:
            silence_limit: Sekunden Stille nach Sprache, die den Satz beenden (Standard aus Konstruktor).
            start_timeout: Sekunden, die auf Sprechbeginn gewartet wird (None = max_duration).
            threshold: Lautstärke-Schwelle für den RMS-Fallback (None = aus Umgebung messen).
        Returns:
            int16-Array mit der Aufnahme oder None, wenn nichts gesprochen wurde.
        """
        silence_limit = self.silence_limit if silence_limit is None else silence_limit
        start_timeout = max_duration if start_timeout is None else start_timeout
        use_vad = self.vad is not None
        if not use_vad and threshold is None:
            threshold = self.speech_threshold(self.measure_ambient(stream))
        start_level = threshold or 0.012
        keep_level = start_level * 0.55  # Hysterese: einmal begonnene Sprache endet erst deutlich leiser
        self._reset_vad()

        # Zeit wird über die gelesenen Samples gemessen (deterministisch, unabhängig von Puffern)
        audio_chunks = []
        silence_start = None
        speech_start = None
        elapsed = 0.0
        while True:
            chunk, _ = stream.read(self.chunk_size)
            elapsed += len(chunk) / self.sample_rate
            orb.pcm_level(chunk, "mic")

            if use_vad:
                prob = self.speech_probability(chunk)
                if prob is None:
                    prob = 1.0 if rms(chunk) > start_level else 0.0
                is_speech = prob >= (self.vad_threshold if speech_start is None else self.vad_threshold * 0.7)
            else:
                level = rms(chunk)
                is_speech = level > (start_level if speech_start is None else keep_level)

            if speech_start is None:
                # Vorlauf behalten, damit der Wortanfang nicht abgeschnitten wird
                audio_chunks.append(chunk)
                audio_chunks = audio_chunks[-6:]
                if is_speech:
                    speech_start = elapsed
                elif elapsed >= start_timeout:
                    return None
                continue

            audio_chunks.append(chunk)
            if is_speech:
                silence_start = None
            else:
                silence_start = elapsed if silence_start is None else silence_start
                if elapsed - silence_start >= silence_limit:
                    break
            if elapsed - speech_start >= max_duration:
                break

        return np.concatenate(audio_chunks, axis=0)

    # ── Erkennung ────────────────────────────────────────────────────────────

    def recognize(self, recording: np.ndarray) -> str:
        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(recording.astype(np.int16).tobytes())
        wav_io.seek(0)
        try:
            with sr.AudioFile(wav_io) as source:
                audio_data = self.recognizer.record(source)
            return self.recognizer.recognize_google(audio_data, language=self.language).strip()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            console.print(f"[bold red]Spracherkennung nicht erreichbar:[/bold red] {e}")
            return ""
        except TimeoutError:
            console.print(f"[bold red]Spracherkennung antwortet nicht[/bold red] (> {STT_TIMEOUT_SECONDS} s).")
            return ""
        except OSError as e:  # z.B. FLAC-Konverter fehlt oder läuft nicht (Apple Silicon: brew install flac)
            console.print(f"[bold red]Spracherkennung fehlgeschlagen:[/bold red] {e}")
            return ""

    # ── Komfort: eigener Stream (Konsolenbefehl 'v') ─────────────────────────

    def record_and_recognize(self, max_duration: float = 30.0, silence_limit: Optional[float] = None) -> str:
        console.print("[bold yellow]🎤 Sprich jetzt ...[/bold yellow]")
        try:
            with platform_utils.open_audio_stream(
                lambda: sd.InputStream(samplerate=self.sample_rate, channels=1, dtype="int16")
            ) as stream:
                recording = self.record_from_stream(stream, max_duration, silence_limit, start_timeout=8.0)
        except Exception as e:
            console.print(f"[bold red]Fehler bei Sprachaufnahme:[/bold red] {e}")
            return ""
        if recording is None:
            console.print("[dim]Keine Sprache erkannt.[/dim]")
            return ""
        console.print("[dim]Analysiere ...[/dim]")
        text = self.recognize(recording)
        if text:
            console.print(f"[bold green]Erkannt:[/bold green] {text}")
        else:
            console.print("[dim]Konnte nichts verstehen.[/dim]")
        return text
