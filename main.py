import sys
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from config import config
from core.agent import JarvisAgent
from core.voice import VoiceEngine
from core.voice_input import VoiceInputListener

console = Console()

def print_banner():
    banner_text = """
[bold cyan]    ██╗ █████╗ ██████╗ ██╗   ██╗██╗███╗   ██╗
    ██║██╔══██╗██╔══██╗██║   ██║██║████╗  ██║
    ██║███████║██████╔╝██║   ██║██║██╔██╗ ██║
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║██║╚██╗██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║██║ ╚████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚═╝  ╚═══╝[/bold cyan]
    [bold white]Dein persönlicher KI-Assistent für macOS[/bold white]
    """
    console.print(banner_text)
    
    status = [
        f"🤖 [bold yellow]LLM Engine:[bold yellow] {'[green]Gemini (gemini-flash-latest)[/green]' if config.GEMINI_API_KEY and config.GEMINI_API_KEY != 'your_gemini_api_key_here' else '[yellow]Bereit (Wartet auf GEMINI_API_KEY in .env)[/yellow]'}",
        f"📅 [bold yellow]Kalender:[bold yellow] [green]Aktiv[/green] (Lokale Datenspeicherung)",
        f"✉️  [bold yellow]E-Mail:[bold yellow] {'[green]Konfiguriert[/green]' if config.EMAIL_ACCOUNT else '[dim]Wartet auf Zugangsdaten in .env[/dim]'}",
        f"🏠 [bold yellow]Home Assistant:[bold yellow] {'[green]Verbunden[/green]' if config.HA_TOKEN else '[dim]Wartet auf HA_TOKEN in .env[/dim]'}",
        f"🔍 [bold yellow]Web-Suche:[bold yellow] [green]Aktiv[/green] (DuckDuckGo / Brave Search)",
        f"🎵 [bold yellow]Spotify:[bold yellow] [green]Aktiv[/green] (Mac-Steuerung via AppleScript)",
        f"🔊 [bold yellow]Sprachausgabe:[bold yellow] {'[green]Aktiv (Unterbrechbar per Reinreden)[/green]' if config.TTS_ENABLED else '[red]Deaktiviert[/red]'}",
        f"🎤 [bold yellow]Spracheingabe:[bold yellow] [green]Bereit[/green] (Tippe [bold cyan]'v'[/bold cyan] oder [bold cyan]'voice'[/bold cyan] zum Sprechen)"
    ]


    
    console.print(Panel("\n".join(status), title="[bold green]Systemstatus[/bold green]", border_style="cyan"))

def show_help():
    help_text = """
[bold yellow]Beispiel-Befehle:[bold yellow]
- [cyan]"v"[/cyan] oder [cyan]"voice"[/cyan] (Starte Spracheingabe über dein Mikrofons)
- [cyan]"Welche Termine habe ich diese Woche?"[/cyan] (Kalender prüfen)
- [cyan]"Neuer Termin: Zahnarzt am 2026-08-15 um 14:00 Uhr"[/cyan] (Termin eintragen)
- [cyan]"Zeige ungelesene E-Mails"[/cyan] (Mail-Postfach abrufen)
- [cyan]"Wie ist der Status im Smart Home?"[/cyan] (Home Assistant abfragen)
- [cyan]"Schalte das Licht im Bad ein"[/cyan] (Smart Home Steuerung)
- [cyan]"Suche im Web nach den neuesten Tech-News"[/cyan] (Web-Suche)
- [cyan]"Wie spät ist ist es?"[/cyan] (System-Uhrzeit)
- [cyan]"Sag: Guten Morgen Colin!"[/cyan] (Sprachausgabe)
- [cyan]"hilfe"[/cyan] (Hilfe anzeigen)
- [cyan]"exit"[/cyan] oder [cyan]"quit"[/cyan] (Beenden)
    """
    console.print(Panel(help_text, title="[bold blue]Hilfe & Befehle[/bold blue]", border_style="blue"))

def main():
    print_banner()
    agent = JarvisAgent()
    voice = VoiceEngine()
    voice_listener = VoiceInputListener()
    
    console.print("[dim]Gib einen Befehl ein, tippe 'v' für Spracheingabe oder 'hilfe'. Zum Beenden 'exit'.[/dim]\n")

    while True:
        try:
            user_input = Prompt.ask("[bold cyan]Jarvis[/bold cyan] [bold white]>[/bold white]").strip()
            
            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "beenden", "cya"):
                console.print("[bold yellow]Auf Wiedersehen![/bold yellow]")
                break
                
            if user_input.lower() in ("hilfe", "help", "?"):
                show_help()
                continue

            # Voice command trigger
            if user_input.lower() in ("v", "voice", "sprechen", "sprache", "mic"):
                recognized_text = voice_listener.record_and_recognize()
                if not recognized_text:
                    continue
                user_input = recognized_text


            # Process query through agent
            with console.status("[bold green]Verarbeite Anfrage...[/bold green]", spinner="dots"):
                response = agent.process_query(user_input)

            console.print(Panel(response, title="[bold green]Jarvis Antwort[/bold green]", border_style="green"))
            
            # Voice output for responses
            if config.TTS_ENABLED:
                voice.speak(response)


        except KeyboardInterrupt:
            console.print("\n[bold yellow]Abgebrochen. Auf Wiedersehen![/bold yellow]")
            sys.exit(0)
        except Exception as e:
            console.print(f"[bold red]Fehler bei der Ausführung:[bold red] {str(e)}")

if __name__ == "__main__":
    main()
