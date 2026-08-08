import re
import subprocess
import threading
import sounddevice as sd
import numpy as np
from config import config

class VoiceEngine:
    def __init__(self):
        self.enabled = config.TTS_ENABLED
        self.voice = config.VOICE_NAME
        self.current_process = None
        self._monitoring = False

    def _clean_text_for_speech(self, text: str) -> str:
        clean = re.sub(r'[\*\#\_\`\[\]]', '', text)
        clean = re.sub(r'https?://\S+', '', clean)
        return clean.strip()

    def stop(self):
        self._monitoring = False
        if self.current_process:
            try:
                self.current_process.terminate()
                self.current_process.kill()
            except Exception:
                pass
            self.current_process = None

    def is_speaking(self) -> bool:
        return self.current_process is not None and self.current_process.poll() is None

    def speak(self, text: str, listen_for_interrupt: bool = True):
        if not config.TTS_ENABLED:
            return
        self.stop()
        cleaned = self._clean_text_for_speech(text)
        if not cleaned:
            return

        def _play():
            try:
                self.current_process = subprocess.Popen(["say", "-v", self.voice, cleaned])
            except Exception:
                try:
                    self.current_process = subprocess.Popen(["say", cleaned])
                except Exception:
                    return

            self._monitoring = True
            
            # If interruptible, monitor microphone in background for user speech
            if listen_for_interrupt:
                self._monitor_barge_in()

            if self.current_process:
                self.current_process.wait()
            self.current_process = None
            self._monitoring = False

        t = threading.Thread(target=_play, daemon=True)
        t.start()

    def _monitor_barge_in(self):
        sample_rate = 16000
        chunk_size = int(sample_rate * 0.1)
        
        try:
            with sd.InputStream(samplerate=sample_rate, channels=1, dtype='int16') as stream:
                while self.is_speaking() and self._monitoring:
                    chunk, _ = stream.read(chunk_size)
                    volume = np.sqrt(np.mean(chunk.astype(np.float32)**2)) / 32768.0
                    
                    # If user speaks loudly (> 0.035), interrupt speech!
                    if volume > 0.035:
                        print("\n[🔊 Unterbrochen durch Spracheingabe!]")
                        self.stop()
                        break
        except Exception:
            pass
