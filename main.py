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
[bold cyan]    ██╗ █████╗ ██████╗ ██╗   ██╗██╗███╗   ██╗
    ██║██╔══██╗██╔══██╗██║   ██║██║████╗  ██║
    ██║███████║██████╔╝██║   ██║██║██╔██╗ ██║
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║██║╚██╗██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║██║ ╚████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚═╝  ╚═══╝[/bold cyan]
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

    spotify_backend = agent.tools["spotify"].backend() if "spotify" in agent.tools else "none"
    spotify_label = {"web": "Web API", "applescript": "AppleScript (Mac)", "none": "nicht eingerichtet"}[spotify_backend]

    rows = [
        f"🤖 [bold yellow]LLM:[/bold yellow] {llm_line}",
        f"📅 [bold yellow]Kalender:[/bold yellow] [green]lokal[/green]",
        f"🏠 [bold yellow]Home Assistant:[/bold yellow] {_yes_no(bool(config.HA_TOKEN), config.HA_URL, 'HA_TOKEN fehlt')}",
        f"✉️  [bold yellow]E-Mail:[/bold yellow] {_yes_no(bool(config.EMAIL_ACCOUNT), config.EMAIL_ACCOUNT, 'nicht konfiguriert')}",
        f"🎵 [bold yellow]Spotify:[/bold yellow] {_yes_no(spotify_backend != 'none', spotify_label, spotify_label)}",
        f"🔍 [bold yellow]Websuche:[/bold yellow] [green]DuckDuckGo[/green]",
        f"🔊 [bold yellow]Sprachausgabe:[/bold yellow] {_yes_no(voice.enabled, voice.label, 'deaktiviert')}",
        f"🎤 [bold yellow]Spracheingabe:[/bold yellow] [green]bereit[/green] [dim](tippe 'v')[/dim]",
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
- [cyan]v[/cyan] / [cyan]voice[/cyan]   Spracheingabe über das Mikrofon
- [cyan]reset[/cyan]        Gesprächsverlauf löschen
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


def main():
    agent = JarvisAgent(
        confirm=confirm_action,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
        on_notice=lambda m: console.print(f"[yellow]⚠ {m}[/yellow]"),
    )
    voice = VoiceEngine()
    voice_listener = None  # wird bei Bedarf geladen (Mikrofon-Bibliotheken)

    print_status(agent, voice)
    console.print("[dim]Befehl eingeben, 'v' für Spracheingabe, 'hilfe' für Beispiele, 'exit' zum Beenden.[/dim]\n")

    while True:
        try:
            user_input = Prompt.ask("[bold cyan]Jarvis[/bold cyan] [bold white]>[/bold white]").strip()
            if not user_input:
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
            if cmd in ("v", "voice", "sprechen", "sprache", "mic"):
                voice.stop()
                if voice_listener is None:
                    from core.voice_input import VoiceInputListener

                    voice_listener = VoiceInputListener(language=config.LANGUAGE)
                user_input = voice_listener.record_and_recognize()
                if not user_input:
                    continue

            voice.stop()  # laufende Ausgabe abbrechen, wenn eine neue Anfrage kommt
            with console.status("[bold green]Denke nach...[/bold green]", spinner="dots"):
                response = agent.process_query(user_input)

            console.print(Panel(response, title="[bold green]Jarvis[/bold green]", border_style="green"))
            voice.speak(response)

        except KeyboardInterrupt:
            voice.stop()
            console.print("\n[bold yellow]Abgebrochen. Bis später![/bold yellow]")
            sys.exit(0)
        except Exception as e:
            console.print(f"[bold red]Fehler:[/bold red] {e}")


if __name__ == "__main__":
    main()
