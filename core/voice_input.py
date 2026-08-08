import io
import time
import wave
import numpy as np
import sounddevice as sd
import speech_recognition as sr
from rich.console import Console

console = Console()

class VoiceInputListener:
    def __init__(self, language: str = "de-DE", sample_rate: int = 16000):
        self.recognizer = sr.Recognizer()
        self.language = language
        self.sample_rate = sample_rate

    def record_and_recognize(self, max_duration: int = 20, silence_limit: float = 0.8) -> str:
        console.print("[bold yellow]🎤 Hör zu... (Kalibriere Hintergrundgeräusche...)[/bold yellow]")
        
        chunk_duration = 0.05  # 50ms chunks for rapid response
        chunk_size = int(self.sample_rate * chunk_duration)
        
        # Measure ambient noise level for 0.3s
        ambient_chunks = []
        with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype='int16') as stream:
            for _ in range(6):
                c, _ = stream.read(chunk_size)
                ambient_chunks.append(c)
        
        amb_data = np.concatenate(ambient_chunks, axis=0).astype(np.float32)
        ambient_rms = np.sqrt(np.mean(amb_data**2)) / 32768.0
        
        # Speech threshold is 2.5x the ambient background noise floor
        speech_threshold = max(0.012, ambient_rms * 2.5)
        
        console.print(f"[bold yellow]🎤 Sprich jetzt frei! (Schwellenwert: {speech_threshold:.3f})[/bold yellow]")

        audio_chunks = []
        silence_start = None
        has_spoken = False
        start_time = time.time()

        try:
            with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype='int16') as stream:
                while True:
                    chunk, _ = stream.read(chunk_size)
                    audio_chunks.append(chunk)
                    
                    # Calculate chunk volume
                    volume = np.sqrt(np.mean(chunk.astype(np.float32)**2)) / 32768.0
                    
                    if volume > speech_threshold:
                        has_spoken = True
                        silence_start = None
                    else:
                        if has_spoken:
                            if silence_start is None:
                                silence_start = time.time()
                            elif time.time() - silence_start >= silence_limit:
                                # Silence detected after speech -> stop immediately!
                                break

                    # Timeout limit
                    if time.time() - start_time >= max_duration:
                        break

            if not audio_chunks or not has_spoken:
                console.print("[dim]Keine Sprache erkannt.[/dim]")
                return ""

            console.print("[dim]Sprechen beendet. Analysiere Audio...[/dim]")
            recording = np.concatenate(audio_chunks, axis=0)

            # Convert numpy array to WAV bytes
            wav_io = io.BytesIO()
            with wave.open(wav_io, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.sample_rate)
                wf.writeframes(recording.tobytes())

            wav_io.seek(0)

            with sr.AudioFile(wav_io) as source:
                audio_data = self.recognizer.record(source)
                text = self.recognizer.recognize_google(audio_data, language=self.language)
                console.print(f"[bold green]Erkannt:[bold green] '{text}'")
                return text

        except sr.UnknownValueError:
            console.print("[bold red]Sprache konnte nicht verstanden werden.[/bold red]")
            return ""
        except Exception as e:
            console.print(f"[bold red]Fehler bei Sprachaufnahme:[bold red] {str(e)}")
            return ""
