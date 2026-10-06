import json
import logging
import sys

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, InvalidResponse, Prompt

from config import config
from core import orb, platform_utils
from core.agent import JarvisAgent
from core.log import rotate_native_log, setup_logging
from core.llm import ToolCall
from core.voice import VoiceEngine
from tools.base import ToolResult

# Windows-Konsolen (v.a. beim Umleiten) nutzen sonst cp1252 und scheitern an Emojis/Boxzeichen
if sys.platform.startswith("win"):
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

LOG_PATH = setup_logging(config.DATA_DIR)
log = logging.getLogger("jarvis")

# PortAudio (PaMacCore) schreibt bei veralteter Geräteliste Warnungen direkt auf fd 1/2 –
# dauerhaft in data/native.log umleiten, Pythons eigene Ausgaben bleiben sichtbar.
if sys.platform == "darwin":
    platform_utils.silence_native_output(str(rotate_native_log(config.DATA_DIR)))

console = Console()

BANNER = """
[bold cyan]     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝[/bold cyan]
    [bold white]Dein persönlicher KI-Assistent[/bold white]  [dim]({platform})[/dim]
"""


def _yes_no(configured: bool, yes: str, no: str) -> str:
    return f"[green]{yes}[/green]" if configured else f"[dim]{no}[/dim]"


def print_status(agent: JarvisAgent, voice: VoiceEngine):
    console.print(BANNER.format(platform=config.platform_name))

    if agent.provider:
        llm_line = f"[green]{agent.provider.describe()}[/green]"
        if agent.fallback:
            llm_line += f"  [dim]Fallback: {agent.fallback.describe()}[/dim]"
    else:
        llm_line = "[red]nicht konfiguriert[/red]"

    spotify_tool = agent.tools.get("spotify")
    spotify_backend = spotify_tool.backend() if spotify_tool else "none"
    spotify_label = {"web": "Web API", "applescript": "AppleScript (Mac)", "none": "nicht eingerichtet"}[spotify_backend]
    if spotify_backend == "web" and not spotify_tool.is_linked():
        spotify_label = "Web API, [yellow]noch nicht verknüpft – tippe 'spotify login'[/yellow]"

    orb_off = "aus [dim](tippe 'orb')[/dim]"
    rows = [
        f"🤖 [bold yellow]LLM:[/bold yellow] {llm_line}",
        f"📅 [bold yellow]Kalender:[/bold yellow] [green]lokal[/green]",
        f"🏠 [bold yellow]Home Assistant:[/bold yellow] {_yes_no(bool(config.HA_TOKEN), config.HA_URL, 'HA_TOKEN fehlt')}",
        f"✉️  [bold yellow]E-Mail:[/bold yellow] {_yes_no(bool(config.EMAIL_ACCOUNT), config.EMAIL_ACCOUNT, 'nicht konfiguriert')}",
        f"🎵 [bold yellow]Spotify:[/bold yellow] {_yes_no(spotify_backend != 'none', spotify_label, spotify_label)}",
        f"🔍 [bold yellow]Websuche:[/bold yellow] [green]DuckDuckGo[/green]",
        f"🧠 [bold yellow]Gedächtnis:[/bold yellow] [green]{agent.memory.count_facts() if agent.memory else 0} Fakten[/green] [dim]({config.MEMORY_DB.name}, tippe 'memory')[/dim]",
        f"🔊 [bold yellow]Sprachausgabe:[/bold yellow] {_yes_no(voice.enabled, voice.label, 'deaktiviert')}",
        f"🎤 [bold yellow]Sprachmodus:[/bold yellow] [green]Wake-Word „Hey Jarvis“[/green] [dim](tippe 'wake' oder VOICE_MODE_ON_START=true)[/dim]",
        f"🔮 [bold yellow]Orb:[/bold yellow] {_yes_no(orb.bus.enabled, orb.bus.address, orb_off)}",
    ]
    for note in agent.notes:
        rows.append(f"⚠️  [yellow]{note}[/yellow]")

    console.print(Panel("\n".join(rows), title="[bold green]Systemstatus[/bold green]", border_style="cyan"))


def show_help():
    help_text = """
[bold yellow]Beispiele:[/bold yellow]
- [cyan]Welche Termine habe ich diese Woche?[/cyan]
- [cyan]Trag morgen um 14 Uhr Zahnarzt ein[/cyan]
- [cyan]Schalte das Licht im Bad aus[/cyan]  /  [cyan]Mach die Küche auf 40 Prozent[/cyan]
- [cyan]Zeige meine ungelesenen Mails[/cyan]
- [cyan]Spiel Bohemian Rhapsody auf Spotify[/cyan]
- [cyan]Wie wird das Wetter morgen in Berlin?[/cyan]
- [cyan]Öffne YouTube mit Lo-Fi Beats[/cyan]

[bold yellow]Befehle:[/bold yellow]
- [cyan]wake[/cyan]         Sprachmodus: dauerhaft lauschen, "Hey Jarvis" sagen, fragen (Strg+C beendet)
- [cyan]v[/cyan] / [cyan]voice[/cyan]   einmalige Spracheingabe über das Mikrofon
- [cyan]orb[/cyan] / [cyan]orb aus[/cyan]  schwebenden Orb starten / schließen (geht auch per Sprache:
                 "Schalte den Orb ein"; schließt sich mit Jarvis)
- [cyan]reset[/cyan]        Gesprächsverlauf löschen
- [cyan]spotify login[/cyan]  Spotify einmalig mit deinem Konto verknüpfen
- [cyan]kosten[/cyan]       Token-Verbrauch und geschätzte Kosten dieser Sitzung
- [cyan]memory[/cyan]       gespeicherte Fakten anzeigen (Jarvis merkt sich Dinge selbst, du kannst
                 ihm aber auch sagen: "Merk dir, dass ..." oder "Vergiss, dass ...")
- [cyan]hilfe[/cyan]        diese Hilfe
- [cyan]exit[/cyan]         beenden
"""
    console.print(Panel(help_text, title="[bold blue]Hilfe[/bold blue]", border_style="blue"))


class _JaNein(Confirm):
    """Ja/Nein-Abfrage, die neben y/n auch j/ja/nein/yes/no versteht."""

    validate_error_message = "[prompt.invalid]Bitte mit j (ja) oder n (nein) antworten"

    def make_prompt(self, default):  # type: ignore[override]
        prompt = self.prompt.copy()
        prompt.end = ""
        prompt.append(" ")
        prompt.append("[j/n]", "prompt.choices")
        prompt.append(" ")
        prompt.append("(n)" if default is False else "(j)", "prompt.default")
        prompt.append(self.prompt_suffix)
        return prompt

    def process_response(self, value: str) -> bool:
        answer = value.strip().lower()
        if answer in ("j", "ja", "y", "yes"):
            return True
        if answer in ("n", "nein", "no"):
            return False
        raise InvalidResponse(self.validate_error_message)


def _active_lives() -> list:
    """Laufende Rich-Live-Anzeigen (z.B. der 'Denke nach...'-Spinner) dieser Konsole."""
    stack = getattr(console, "_live_stack", None)  # rich >= 14
    if stack is not None:
        return list(stack)
    single = getattr(console, "_live", None)  # ältere rich-Versionen
    return [single] if single is not None else []


def confirm_action(prompt: str) -> bool:
    # Während das LLM arbeitet, läuft ein Spinner (console.status). Der zeichnet die eigene Zeile
    # ständig neu und überschreibt dabei die Eingabezeile der Nachfrage – sie ist dann unsichtbar.
    # Deshalb alle Live-Anzeigen anhalten, fragen und danach wieder starten.
    lives = _active_lives()
    for live in reversed(lives):
        live.stop()
    try:
        console.print(Panel(escape(prompt), title="[bold yellow]Bestätigung nötig[/bold yellow]", border_style="yellow"))
        return _JaNein.ask("Ausführen?", default=False, console=console)
    except (EOFError, KeyboardInterrupt):
        console.print("[dim]Keine Eingabe – Aktion nicht ausgeführt.[/dim]")
        return False
    finally:
        for live in lives:
            live.start()


def on_tool_call(call: ToolCall):
    args = json.dumps(call.arguments, ensure_ascii=False)
    if len(args) > 120:
        args = args[:117] + "..."
    if call.name == "screen":  # gut sichtbar: Jarvis schaut gerade auf den Bildschirm
        console.print("  [bold cyan]📸 Schaue auf deinen Bildschirm …[/bold cyan]")
    console.print(f"  [dim]⚙ {escape(call.name)} {escape(args)}[/dim]")
    orb.tool("Bildschirm" if call.name == "screen" else call.name)


def on_tool_result(call: ToolCall, result: ToolResult):
    icon = "[green]✓[/green]" if result.success else "[red]✗[/red]"
    first_line = result.output.strip().splitlines()[0] if result.output.strip() else ""
    if len(first_line) > 100:
        first_line = first_line[:97] + "..."
    console.print(f"  [dim]{icon} {escape(first_line)}[/dim]")


def run_voice_mode(agent: JarvisAgent, voice: VoiceEngine) -> bool:
    """Startet den Wake-Word-Sprachmodus. Gibt False zurück, wenn er nicht verfügbar ist."""
    try:
        from core.voice_loop import VoiceLoop

        with console.status("[dim]Lade Wake-Word-Modell ...[/dim]"):
            loop = VoiceLoop(
                agent,
                voice,
                console=console,
                follow_up_seconds=config.FOLLOW_UP_SECONDS,
                barge_in_threshold=config.BARGE_IN_THRESHOLD,
            )
    except Exception as e:
        console.print(f"[bold red]Sprachmodus nicht verfügbar:[/bold red] {escape(str(e))}")
        console.print("[dim]Mikrofon angeschlossen? Pakete installiert (uv sync)?[/dim]")
        return False
    loop.run()
    console.print("[dim]Sprachmodus beendet. Du bist wieder in der Texteingabe ('wake' startet ihn erneut).[/dim]")
    return True


def main():
    voice_mode = config.VOICE_MODE_ON_START or "--voice" in sys.argv[1:]
    agent = JarvisAgent(
        confirm=confirm_action,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
        on_notice=lambda m: console.print(f"[yellow]⚠ {m}[/yellow]"),
    )
    voice = VoiceEngine()
    if config.ORB_ENABLED:
        orb_error = orb.start(port=config.ORB_PORT)
        if orb_error:
            agent.notes.append(orb_error)
    voice_listener = None  # wird bei Bedarf geladen (Mikrofon-Bibliotheken)

    log.info("Jarvis gestartet (%s, LLM: %s)", config.platform_name, agent.provider.describe() if agent.provider else "keins")
    print_status(agent, voice)
    if voice_mode:
        try:
            run_voice_mode(agent, voice)
        except Exception as e:  # sonst beendet ein Fehler im Sprachmodus beim Start ganz Jarvis
            log.exception("Sprachmodus beim Start abgebrochen")
            console.print(f"[bold red]Sprachmodus abgebrochen:[/bold red] {escape(str(e))}")
    console.print("[dim]Befehl eingeben, 'wake' für den Sprachmodus, 'v' für eine Spracheingabe, 'hilfe', 'exit'.[/dim]\n")

    while True:
        try:
            user_input = Prompt.ask("[bold cyan]Jarvis[/bold cyan] [bold white]>[/bold white]").strip()
            if not user_input:
                if voice.is_speaking():
                    voice.stop()
                    console.print("[dim]Sprachausgabe gestoppt.[/dim]")
                continue
            cmd = user_input.lower()

            if cmd in ("exit", "quit", "beenden", "tschüss", "cya"):
                voice.stop()
                console.print("[bold yellow]Bis später![/bold yellow]")
                break
            if cmd in ("hilfe", "help", "?"):
                show_help()
                continue
            if cmd in ("reset", "neu", "clear"):
                agent.reset()
                console.print("[dim]Gesprächsverlauf gelöscht.[/dim]")
                continue
            if cmd in ("spotify login", "spotify verknüpfen"):
                voice.stop()
                ok, message = agent.tools["spotify"].login()
                console.print(f"[{'green' if ok else 'red'}]{message}[/]")
                continue
            if cmd in ("kosten", "usage", "verbrauch"):
                console.print(f"[dim]Diese Sitzung: {agent.usage_summary(agent.session_usage)}[/dim]")
                continue
            if cmd in ("memory", "gedächtnis", "erinnerungen"):
                facts = agent.memory.list_facts() if agent.memory else []
                body = "\n".join(f"[dim]#{f['id']}[/dim] [cyan]{escape(f['category'])}[/cyan]  {escape(f['content'])}" for f in facts) or "[dim]Noch leer.[/dim]"
                console.print(Panel(body, title="[bold magenta]Gedächtnis[/bold magenta]", border_style="magenta"))
                continue
            if cmd in ("orb", "orb an", "orb aus"):
                ok, message = orb.close_window() if cmd == "orb aus" else orb.open_window(port=config.ORB_PORT)
                console.print(f"[{'green' if ok else 'red'}]{message}[/]")
                continue
            if cmd in ("wake", "sprachmodus", "hey", "zuhören", "listen"):
                voice.stop()
                run_voice_mode(agent, voice)
                continue
            if cmd in ("v", "voice", "sprechen", "sprache", "mic"):
                voice.stop()
                if voice_listener is None:
                    from core.voice_input import VoiceInputListener

                    voice_listener = VoiceInputListener(
                        language=config.LANGUAGE,
                        vad_threshold=config.VAD_THRESHOLD,
                        silence_limit=config.SILENCE_LIMIT_SECONDS,
                    )
                orb.state("listening")
                user_input = voice_listener.record_and_recognize()
                if not user_input:
                    orb.state("idle")
                    continue

            voice.stop()  # laufende Ausgabe abbrechen, wenn eine neue Anfrage kommt
            orb.transcript("user", user_input)
            orb.state("thinking")
            try:
                with console.status("[bold green]Denke nach... [dim](Strg+C bricht diese Frage ab)[/dim][/bold green]", spinner="dots"):
                    response = agent.process_query(user_input)
            except KeyboardInterrupt:
                orb.state("idle")
                console.print("[yellow]Frage abgebrochen.[/yellow]")
                continue

            console.print(Panel(escape(response), title="[bold green]Jarvis[/bold green]", border_style="green"))
            if agent.last_usage.calls:
                console.print(f"  [dim]{agent.usage_summary(agent.last_usage)}[/dim]")
            orb.transcript("assistant", response)
            voice.speak(response)
            if voice.is_speaking():
                console.print("[dim]Enter stoppt die Sprachausgabe.[/dim]")
            else:
                orb.state("idle")

        except KeyboardInterrupt:
            voice.stop()
            console.print("\n[bold yellow]Abgebrochen. Bis später![/bold yellow]")
            sys.exit(0)
        except Exception as e:
            log.exception("Unerwarteter Fehler in der Hauptschleife")
            orb.error(str(e)[:120])
            orb.state("idle")
            console.print(f"[bold red]Fehler:[/bold red] {escape(str(e))} [dim](Details in {LOG_PATH})[/dim]")


if __name__ == "__main__":
    main()
