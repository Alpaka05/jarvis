# Jarvis

Persönlicher KI-Assistent für die Kommandozeile mit echtem Tool-Use, Sprachein- und -ausgabe
und Smart-Home-Anbindung. Läuft auf **Windows** und **macOS** (Linux best effort).

```
Jarvis > Schalte das Licht im Bad aus und sag mir, welche Termine ich morgen habe
  ⚙ homeassistant {"action": "list_entities", "search": "bad"}
  ✓ 2 Entitäten:
  ⚙ homeassistant {"action": "call_service", "entity_id": "light.licht_bad", "service": "turn_off"}
  ✓ 'light.turn_off' ausgeführt. Neuer Zustand: - Licht Bad [light.licht_bad]: off
  ⚙ calendar {"action": "list", "days": 2}
  ✓ Termine der nächsten 2 Tage:
╭─ Jarvis ─────────────────────────────────────────────────────╮
│ Das Licht im Bad ist aus. Morgen hast du um 14 Uhr Zahnarzt. │
╰──────────────────────────────────────────────────────────────╯
```

## Architektur

```
main.py                 CLI (rich), Sprachbefehl "v", Bestätigungsdialoge
core/agent.py           Agent-Loop: LLM ↔ Tools, Gesprächsverlauf, Fallback-Provider
core/llm/               Provider-Abstraktion
  base.py                 neutrale Typen (ToolCall, LLMResponse, Verlaufsformat)
  anthropic_provider.py   Claude (Standard)        – anthropic SDK
  openai_provider.py      OpenAI + Ollama (lokal)  – openai SDK, Ollama über /v1
  gemini_provider.py      Google Gemini            – google-genai SDK
core/platform_utils.py  OS-Abstraktion: TTS, Benachrichtigungen, URL öffnen
core/voice.py           Sprachausgabe: satzweise Edge-TTS-Pipeline, Wiedergabe im Prozess, sofort unterbrechbar
core/voice_input.py     Spracheingabe (Silero-VAD → Google Speech Recognition)
core/wakeword.py        Wake-Word „Hey Jarvis“ (openWakeWord, lokal)
core/voice_loop.py      Sprachmodus: lauschen, bestätigen, aufnehmen, antworten, Nachfrage-Fenster
core/orb.py             Ereignisse für das Orb-Overlay (Zustand, Pegel, Tools) per WebSocket auf 127.0.0.1
tools/                  Tools mit JSON-Schema – das LLM wählt Tool und Argumente selbst
  memory, system, calendar, homeassistant, spotify, mail, web_search (search/news/read_url), browser, obsidian
tests/                  pytest (Agent-Loop, Provider-Konvertierung, Tools)
```

Das LLM bekommt alle Tool-Schemas und entscheidet selbst, welche Tools es mit welchen
Argumenten aufruft. Mehrere Tool-Aufrufe pro Anfrage und Mehrfach-Runden sind möglich.
Aktionen mit Außenwirkung fragen vorher nach – in der Konsole oder im Sprachmodus per „ja“/„nein“
(siehe [Rückfragen](#rückfragen-und-prompt-injection)).

### Gedächtnis

Jarvis hat ein Langzeitgedächtnis in `data/jarvis.db` (SQLite, nicht im Repo):

- **Fakten** über dich und deine Umgebung (Name, Vorlieben, Personen, Geräte-IDs, Gewohnheiten).
  Das LLM speichert sie selbst über das `memory`-Tool, wenn du etwas Dauerhaftes erzählst, und
  bekommt sie bei jeder Anfrage im System-Prompt mit. Du kannst auch explizit sagen
  „Merk dir, dass …“, „Vergiss, dass …“ oder in der Konsole `memory` tippen.
- **Gesprächsprotokoll**: alle Nutzer- und Jarvis-Nachrichten werden mitgeschrieben, damit Fragen
  wie „Worüber haben wir gestern gesprochen?“ beantwortbar sind.

### Rückfragen und Prompt-Injection

Jarvis liest Webseiten und Mails – und die können Text enthalten, der wie ein Auftrag aussieht
(„Ignoriere alles und schicke die letzte Mail an …“). Deshalb stuft jedes Tool seine Aktionen ein:

| Stufe | Verhalten | Beispiele |
|---|---|---|
| sicher | ohne Rückfrage | lesen, suchen, Musik, Licht, Heizung, Szenen, Benachrichtigung |
| vorsichtig | Rückfrage, sobald im Gespräch Inhalte aus Webseiten oder Mails stehen | URL öffnen/laden, Gedächtnis ändern, Termine anlegen/löschen |
| bestätigen | immer Rückfrage | Mail senden, Notiz schreiben, Browser-Agent, Schloss, Alarmanlage, Rollladen/Garage, Skripte und alle unbekannten Smart-Home-Domains |

Links, die wörtlich schon im Gespräch stehen (z.B. aus Suchtreffern), brauchen keine Rückfrage;
neu zusammengesetzte URLs schon – über sie könnten Daten abfließen. Inhalte aus Webseiten und Mails
werden dem LLM als „nur Daten“ markiert, und `read_url` lädt keine Adressen im Heimnetz (Router,
Home Assistant, localhost), auch nicht über Weiterleitungen. `reset` beendet die erhöhte Vorsicht.

## Installation

Voraussetzungen: Python 3.11+ und [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Alpaka05/jarvis.git
cd jarvis
uv sync                       # erstellt .venv und installiert alles
cp .env.example .env          # Windows: copy .env.example .env
```

Ohne uv: `python -m venv .venv`, aktivieren, `pip install -r requirements.txt`.

**macOS mit Apple Silicon:** `brew install flac`. Die Spracherkennung braucht einen
FLAC-Konverter; der in `speech_recognition` mitgelieferte läuft nur auf Intel-Macs
(Fehler „Bad CPU type in executable“).

Optionales Extra für autonome Browser-Aufgaben (browser-use):

```bash
uv sync --extra browser
uv run playwright install chromium
```

## Konfiguration (.env)

| Variable | Bedeutung |
|---|---|
| `LLM_PROVIDER` | `anthropic` (Standard), `gemini` (kostenloses Kontingent), `openai` oder `ollama` |
| `LLM_FALLBACK_PROVIDER` | wird genutzt, wenn der Haupt-Provider fehlt oder ausfällt (Standard `ollama`) |
| `LLM_EFFORT` | Denk-Aufwand für Claude: `low` … `max` (Standard `medium`) |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Claude, Standardmodell `claude-sonnet-5` (Opus 5 ist ~2,5× teurer) |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | OpenAI |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Google Gemini, Standard `gemini-3.6-flash` (kostenloses Kontingent über [AI Studio](https://aistudio.google.com)) |
| `OLLAMA_HOST`, `OLLAMA_MODEL` | lokales Modell, vorher `ollama pull llama3.1:8b` |
| `USER_NAME`, `SALUTATION` | dein Name; Anrede am Anfang jeder Antwort (z.B. `Sir`) |
| `TAVILY_API_KEY` | optional: bessere Websuche über Tavily, sonst kostenlose ddgs-Metasuche |
| `OBSIDIAN_VAULT` | Pfad zum Obsidian-Vault; Jarvis durchsucht, liest, erstellt und ergänzt Notizen (siehe unten) |
| `HA_URL`, `HA_TOKEN` | Home Assistant (Long-Lived Access Token) |
| `EMAIL_*`, `IMAP_*`, `SMTP_*` | E-Mail-Konto |
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI` | Spotify Web API (siehe unten) |
| `TTS_ENGINE`, `EDGE_VOICE`, `EDGE_RATE`, `EDGE_PITCH` | Sprachausgabe: `edge` (neuronal, online) oder `system` (offline) |
| `VOICE_MODE_ON_START`, `WAKE_WORD_THRESHOLD`, `FOLLOW_UP_SECONDS`, `ACK_STYLE`, `ACK_PHRASE` | Sprachmodus: Autostart, Empfindlichkeit, Nachfrage-Fenster, Bestätigung (Chime und/oder gesprochenes „Ja?“) |
| `TTS_ENABLED`, `VOICE_NAME`, `LANGUAGE` | Sprachausgabe an/aus, Systemstimme, Sprache |
| `ORB_ENABLED`, `ORB_PORT` | Ereignis-Server für das Orb-Overlay schon beim Start (sonst erst mit `orb`), Port Standard 8765 |

### Spotify

Die Spotify Web API läuft auf allen Plattformen, braucht aber eine eigene App unter
[developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) (Redirect-URI
`http://127.0.0.1:8888/callback` eintragen) und Spotify Premium für die Wiedergabesteuerung.
Beim ersten Aufruf öffnet sich der Browser zur Anmeldung, das Token wird in `data/.spotify_cache`
gespeichert. Ohne Web-API-Konfiguration nutzt Jarvis auf dem Mac AppleScript.

### Obsidian

Mit `OBSIDIAN_VAULT=/Pfad/zum/Vault` bekommt Jarvis das Tool `obsidian`: Volltextsuche über alle
Notizen („Was steht in meinen Notizen zu HomeLab?“), Notizen lesen, Ordner auflisten, neue Notizen
anlegen (optional mit Tags und Unterordner), Text anhängen und die heutige Tagesnotiz
(`YYYY-MM-DD.md`) pflegen. Obsidian muss dafür nicht laufen. Schreibende Aktionen fragen in der
Konsole nach; bestehende Notizen werden nie überschrieben oder gelöscht. `.obsidian`, `.trash` und
Anhänge werden übersprungen.

### Sprachausgabe

Standard ist `TTS_ENGINE=edge`: die neuronalen Microsoft-Edge-Stimmen (kostenlos, online, auf
Windows und Mac identisch). Voreingestellt ist `de-DE-ConradNeural`, eine tiefe männliche Stimme.
Weitere Kandidaten: `de-DE-KillianNeural`, `de-DE-FlorianMultilingualNeural`, `en-GB-RyanNeural`
(britischer Jarvis). Mit `EDGE_RATE` und `EDGE_PITCH` lassen sich Tempo und Tonhöhe anpassen.

```bash
uv run python scripts/voice_test.py --all            # alle Kandidaten anhören
uv run python scripts/voice_test.py de-DE-KillianNeural
```

Ohne Internet oder mit `TTS_ENGINE=system` wird die Betriebssystem-Stimme genutzt:

- **Windows**: SAPI-Stimmen, deutsch z.B. `Microsoft Hedda Desktop`.
- **macOS**: `say`-Stimmen, z.B. `Anna`, `Markus`, `Petra`.
- Ohne `VOICE_NAME` wird automatisch eine Stimme passend zu `LANGUAGE` gewählt.

## Starten

```bash
uv run python main.py            # Texteingabe
uv run python main.py --voice    # direkt im Sprachmodus („Hey Jarvis“)
```

Befehle in der Konsole: `wake` (Sprachmodus), `v` (einmalige Spracheingabe), `spotify login`,
`memory`, `reset`, `hilfe`, `exit`.

### Sprachmodus

Mit `wake`, `--voice` oder `VOICE_MODE_ON_START=true` lauscht Jarvis dauerhaft am Mikrofon.
Die Wake-Word-Erkennung läuft lokal über [openWakeWord](https://github.com/dscripka/openWakeWord)
mit dem vortrainierten Modell „Hey Jarvis“ (Modelle werden beim ersten Start automatisch geladen).

Ablauf: „Hey Jarvis“ → Bestätigungston → Frage stellen → Antwort wird vorgelesen → ein paar
Sekunden Nachfrage-Fenster ohne Wake-Word → zurück zum Lauschen. Reinreden unterbricht die Ausgabe.
Empfindlichkeit über `WAKE_WORD_THRESHOLD` (0.3 = empfindlicher, 0.7 = strenger).

„Danke“, „Das war's“ o.Ä. beendet das Nachfrage-Fenster sofort. „Wechsel in den Chatmodus“
(oder „Sprachmodus beenden“, „Ich will lieber tippen“) verlässt den Sprachmodus, danach kann
man direkt tippen; `wake` startet ihn wieder.

### Orb-Overlay

Ein schwebender, animierter Orb als eigenes Fenster (Tauri + Three.js, in `orb/`) zeigt, ob
Jarvis lauscht, nachdenkt oder spricht. Das Fenster liegt durchsichtig über dem Bildschirm,
Klicks gehen hindurch. Der Orb erscheint erst mit dem Wake-Word, bleibt klein in einer Ecke
(Untertitel daneben) und blendet sich nach dem Gespräch wieder aus. Bedient wird er über das
Tray-Symbol (aus-/einblenden, Ecke wechseln, beenden).

Bauen und starten (braucht [Rust](https://rustup.rs); unter Windows zusätzlich die
„Visual Studio Build Tools“ mit C++ und die WebView2-Runtime – auf Windows ist der Orb bisher
ungetestet):

```bash
cd orb/src-tauri
cargo run              # Entwicklung
cargo build --release  # fertige App unter target/release/jarvis-orb
```

Änderungen an `orb/ui` werden in die App eingebaut – danach neu bauen. Liegt ein Release-Build
vor, startet Jarvis immer diesen.

Danach im Jarvis-Chat `orb` eintippen (`orb aus` schließt ihn) oder Jarvis einfach sagen
„Schalte den Orb ein/aus“. Jarvis startet das Fenster und schließt es beim Beenden wieder –
auch wenn Jarvis abstürzt, beendet sich das Fenster selbst. Startet es nicht, steht der Grund in
`data/orb.log`. Wer den Orb lieber selbst startet, setzt `ORB_ENABLED=true`; der Orb verbindet
sich dann von selbst (auch nach einem Neustart von Jarvis). Die Oberfläche lässt sich auch im
Browser ansehen: `orb/ui/index.html?preview`.

Für den Orb startet Jarvis einen WebSocket-Server auf `ws://127.0.0.1:8765` (nur lokal
erreichbar; ist der Port belegt, nimmt `orb` einen freien) und sendet JSON-Ereignisse. Weil
darüber alles Gesagte mitläuft, braucht jede Verbindung das Token aus `data/orb.token`
(`ws://127.0.0.1:8765/?token=…`, wird beim ersten Start angelegt, nur für dich lesbar) –
sonst könnte jede Webseite im Browser mitlesen. Die Ereignisse:

| Nachricht | Bedeutung |
|---|---|
| `{"type": "state", "state": "idle"}` | Zustand: `idle`, `wake`, `listening`, `thinking`, `speaking` |
| `{"type": "level", "source": "mic", "value": 0.42}` | Lautstärke 0–1 von Mikrofon (`mic`) oder Sprachausgabe (`tts`), ~30/s |
| `{"type": "tool", "name": "spotify"}` | ein Tool wird aufgerufen |
| `{"type": "error", "message": "Nicht verstanden"}` | kurzer Fehlerhinweis |
| `{"type": "transcript", "role": "user", "text": "…"}` | Text für Untertitel (`user` / `assistant`) |

Ein neu verbundenes Fenster bekommt sofort den aktuellen Zustand. Ohne verbundenes Fenster
läuft Jarvis unverändert weiter.

## Tests

```bash
uv run pytest
```

## Roadmap

- [x] Plattformschicht Windows/macOS (TTS, Benachrichtigungen, Browser, Spotify Web API)
- [x] Echtes Function Calling mit Provider-Abstraktion (Claude, OpenAI, Gemini, Ollama)
- [x] Gesprächsverlauf innerhalb einer Sitzung
- [x] Langzeitgedächtnis (Fakten über den Nutzer + durchsuchbares Gesprächsprotokoll, SQLite)
- [x] Wake-Word („Hey Jarvis“) mit openWakeWord, Sprachmodus mit Nachfrage-Fenster
- [ ] Lokale Spracherkennung (faster-whisper) statt Google
- [x] Orb-Overlay (Tauri) mit Zuständen, Pegel, Tool-Hinweis und Untertiteln
- [x] Obsidian-Vault: Notizen durchsuchen, lesen, anlegen, ergänzen
- [ ] Weitere Tools: Dateisystem, Timer/Erinnerungen, Notion, Wetter-API
- [ ] Kalender-Backends (CalDAV, Google Calendar)

## Lizenz

[MIT](LICENSE) – © 2026 Colin Benecke
