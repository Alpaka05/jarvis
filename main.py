import json
import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from config import config
from core import platform_utils
from core.agent import JarvisAgent
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
- [cyan]reset[/cyan]        Gesprächsverlauf löschen
- [cyan]spotify login[/cyan]  Spotify einmalig mit deinem Konto verknüpfen
- [cyan]kosten[/cyan]       Token-Verbrauch und geschätzte Kosten dieser Sitzung
- [cyan]memory[/cyan]       gespeicherte Fakten anzeigen (Jarvis merkt sich Dinge selbst, du kannst
                 ihm aber auch sagen: "Merk dir, dass ..." oder "Vergiss, dass ...")
- [cyan]hilfe[/cyan]        diese Hilfe
- [cyan]exit[/cyan]         beenden
"""
    console.print(Panel(help_text, title="[bold blue]Hilfe[/bold blue]", border_style="blue"))


def confirm_action(prompt: str) -> bool:
    console.print(Panel(prompt, title="[bold yellow]Bestätigung nötig[/bold yellow]", border_style="yellow"))
    return Confirm.ask("Ausführen?", default=False)


def on_tool_call(call: ToolCall):
    args = json.dumps(call.arguments, ensure_ascii=False)
    if len(args) > 120:
        args = args[:117] + "..."
    console.print(f"  [dim]⚙ {call.name} {args}[/dim]")


def on_tool_result(call: ToolCall, result: ToolResult):
    icon = "[green]✓[/green]" if result.success else "[red]✗[/red]"
    first_line = result.output.strip().splitlines()[0] if result.output.strip() else ""
    if len(first_line) > 100:
        first_line = first_line[:97] + "..."
    console.print(f"  [dim]{icon} {first_line}[/dim]")


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
        console.print(f"[bold red]Sprachmodus nicht verfügbar:[/bold red] {e}")
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
    voice_listener = None  # wird bei Bedarf geladen (Mikrofon-Bibliotheken)

    print_status(agent, voice)
    if voice_mode:
        run_voice_mode(agent, voice)
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
                body = "\n".join(f"[dim]#{f['id']}[/dim] [cyan]{f['category']}[/cyan]  {f['content']}" for f in facts) or "[dim]Noch leer.[/dim]"
                console.print(Panel(body, title="[bold magenta]Gedächtnis[/bold magenta]", border_style="magenta"))
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
                user_input = voice_listener.record_and_recognize()
                if not user_input:
                    continue

            voice.stop()  # laufende Ausgabe abbrechen, wenn eine neue Anfrage kommt
            with console.status("[bold green]Denke nach...[/bold green]", spinner="dots"):
                response = agent.process_query(user_input)

            console.print(Panel(response, title="[bold green]Jarvis[/bold green]", border_style="green"))
            if agent.last_usage.calls:
                console.print(f"  [dim]{agent.usage_summary(agent.last_usage)}[/dim]")
            voice.speak(response)
            if voice.enabled:
                console.print("[dim]Enter stoppt die Sprachausgabe.[/dim]")

        except KeyboardInterrupt:
            voice.stop()
            console.print("\n[bold yellow]Abgebrochen. Bis später![/bold yellow]")
            sys.exit(0)
        except Exception as e:
            console.print(f"[bold red]Fehler:[/bold red] {e}")


if __name__ == "__main__":
    main()
