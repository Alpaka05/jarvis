import os
from typing import Dict, Any, List
from config import config
from tools import CalendarTool, MailTool, HomeAssistantTool, SearchTool, SystemTool, SpotifyTool, BrowserTool
from tools.base import BaseTool, ToolResult

class JarvisAgent:
    def __init__(self):
        self.tools: Dict[str, BaseTool] = {
            "calendar": CalendarTool(),
            "mail": MailTool(),
            "homeassistant": HomeAssistantTool(),
            "search": SearchTool(),
            "system": SystemTool(),
            "spotify": SpotifyTool(),
            "browser": BrowserTool()
        }


        self.gemini_client = None
        self._init_gemini()

    def _init_gemini(self):
        api_key = config.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        if api_key and api_key != "your_gemini_api_key_here":
            try:
                from google import genai
                self.gemini_client = genai.Client(api_key=api_key)
            except Exception as e:
                print(f"[Warning] Gemini client konnte nicht initialisiert werden: {e}")

    def call_gemini(self, prompt: str, context: str = "") -> str:
        if not self.gemini_client:
            self._init_gemini()
            
        if not self.gemini_client:
            return (
                "Gemini API-Key fehlt noch! Bitte trage deinen `GEMINI_API_KEY` in der `.env`-Datei ein "
                "(kostenlos unter https://aistudio.google.com erhältlich)."
            )
        try:
            full_prompt = (
                "Du bist Jarvis, ein intelligenter, hilfsbereiter KI-Assistent auf einem Mac. "
                "Antworte präzise, freundlich und auf Deutsch.\n"
            )
            if context:
                full_prompt += f"System-Kontext / Werkzeug-Ergebnisse:\n{context}\n\n"
            full_prompt += f"Nutzer-Anfrage: {prompt}"

            # Use gemini-flash-latest which is active and supported on free/paid tiers
            import time
            for attempt in range(2):
                for model_name in ["gemini-flash-latest", "gemini-pro-latest", "gemini-2.0-flash"]:
                    try:
                        response = self.gemini_client.models.generate_content(
                            model=model_name,
                            contents=full_prompt,
                        )
                        return response.text.strip()
                    except Exception as e:
                        if "404" in str(e) or "429" in str(e):
                            continue
                        raise e
                time.sleep(1)
            return "Entschuldigung, die KI-Anfrage hat kurz gedauert. Bitte versuche den Befehl noch einmal."



        except Exception as e:
            return f"Fehler bei Gemini API-Anfrage: {str(e)}"

    def process_query(self, query: str) -> str:
        q_lower = query.lower()

        # 1. Calendar intent
        if any(w in q_lower for w in ["termin", "kalender", "zeitplan", "meeting"]):
            if any(w in q_lower for w in ["eintragen", "erstellen", "neuer", "neu"]):
                res = self.tools["calendar"].execute(action="add", title=query, description="Erstellt über Jarvis CLI")
            else:
                res = self.tools["calendar"].execute(action="list", days=7)
            
            if self.gemini_client:
                return self.call_gemini(query, context=res.output)
            return res.output

        # 2. Mail intent
        elif any(w in q_lower for w in ["mail", "email", "e-mail", "postfach", "nachricht"]):
            if any(w in q_lower for w in ["sende", "schreibe", "versende"]):
                res = self.tools["mail"].execute(action="send", subject="Nachricht von Jarvis", body=query)
            else:
                res = self.tools["mail"].execute(action="read")

            if self.gemini_client:
                return self.call_gemini(query, context=res.output)
            return res.output

        # 3. Home Assistant intent
        elif any(w in q_lower for w in ["licht", "home assistant", "smart home", "lampe", "steckdose", "heizung", "sensor"]):
            target_entity = "light.zuhause"
            if "bad" in q_lower:
                target_entity = "light.licht_bad"
            elif "tv" in q_lower:
                target_entity = "light.strahler_tv"
            elif "küche" in q_lower or "kuche" in q_lower:
                target_entity = "light.strahler_kuche"
            elif "bett" in q_lower:
                target_entity = "light.bett"
            elif "led" in q_lower or "leiste" in q_lower:
                target_entity = "light.led_leisten"

            if any(w in q_lower for w in ["an", "einschalten", "ein"]):
                res = self.tools["homeassistant"].execute(action="turn_on", entity_id=target_entity)
            elif any(w in q_lower for w in ["aus", "ausschalten"]):
                res = self.tools["homeassistant"].execute(action="turn_off", entity_id=target_entity)
            else:
                res = self.tools["homeassistant"].execute(action="get_states")
            return res.output

        # 4. Search intent
        elif any(w in q_lower for w in ["suche", "wetter", "nachrichten", "recherche", "google"]):
            res = self.tools["search"].execute(query=query)
            if self.gemini_client:
                return self.call_gemini(query, context=res.output)
            return res.output

        # 5. Spotify intent
        elif any(w in q_lower for w in ["spotify", "musik", "song", "lied", "abspielen", "wiedergabe", "nächster song", "pause", "stopp"]):
            if any(w in q_lower for w in ["weiter", "nächster", "nächstes"]):
                res = self.tools["spotify"].execute(action="next")
            elif any(w in q_lower for w in ["zurück", "vorheriger"]):
                res = self.tools["spotify"].execute(action="prev")
            elif any(w in q_lower for w in ["pause", "stopp"]):
                res = self.tools["spotify"].execute(action="play")
            elif any(w in q_lower for w in ["lautstärke", "lauter", "leiser"]):
                res = self.tools["spotify"].execute(action="volume", volume=70)
            elif any(w in q_lower for w in ["spiel", "spiele", "suche", "hören"]):
                clean_q = query
                for word in ["Spiele", "spiele", "Spiel", "spiel", "auf Spotify", "auf spotify", "Spotify", "spotify", "das Lied", "das lied", "von", "auf"]:
                    clean_q = clean_q.replace(word, "")
                clean_q = clean_q.strip()
                res = self.tools["spotify"].execute(action="search", query=clean_q or query)
            else:
                res = self.tools["spotify"].execute(action="status")
            return res.output


        # 6. YouTube & Browser / Pizza ordering intent
        elif any(w in q_lower for w in ["youtube", "video", "clip", "film"]):
            res = self.tools["browser"].execute(action="youtube", query=query)
            return res.output

        elif any(w in q_lower for w in ["pizza", "bestell", "lieferando", "bestellen", "browser"]):
            target_query = "Pizza Salami"
            if self.gemini_client:
                prompt_extract = (
                    f"Extrahierte NUR den kurzen Namen der gewünschten Speise (z.B. 'Pizza Salami', 'Pizza Margherita', 'Pizza') "
                    f"aus dieser Anfrage: '{query}'. Antworten NUR mit dem Begriff selbst, keinen Satzzeichen, keinen Zusatztext."
                )
                try:
                    raw_res = self.gemini_client.models.generate_content(
                        model="gemini-flash-latest",
                        contents=prompt_extract
                    ).text.strip().replace('"', '').replace("'", "")
                    if raw_res and len(raw_res) < 40 and not "hilfe" in raw_res.lower():
                        target_query = raw_res
                except Exception:
                    pass

            res = self.tools["browser"].execute(action="pizza", query=target_query)
            return f"🍕 [Gemini hat erkannt: '{target_query}']\n{res.output}"




        # 7. System time / spoken response intent
        elif any(w in q_lower for w in ["zeit", "uhr", "datum", "sprich", "sag", "benachrichtigung"]):
            if any(w in q_lower for w in ["sprich", "sag"]):
                clean_text = query.replace("sprich", "").replace("sag", "").strip()
                res = self.tools["system"].execute(action="speak", text=clean_text or "Hallo! Ich bin Jarvis.")
                return res.output
            elif "benachrichtigung" in q_lower:
                res = self.tools["system"].execute(action="notify", title="Jarvis", message=query)
                return res.output
            else:
                res = self.tools["system"].execute(action="time")
                return res.output

        # 6. General Conversational / LLM query
        else:
            time_res = self.tools["system"].execute(action="time")
            if self.gemini_client:
                return self.call_gemini(query, context=f"Systemzeit: {time_res.output}")
            else:
                return (
                    f"Ich habe deine Anfrage verstanden: '{query}'.\n{time_res.output}\n"
                    f"💡 Tipp: Trage deinen `GEMINI_API_KEY` in der `.env`-Datei ein, um freie Gespräche mit Google Gemini zu aktivieren!"
                )
