//! Jarvis Orb: die Fenster von Jarvis.
//!
//! - „orb“: transparentes, klick-durchlässiges Overlay über dem ganzen Bildschirm (ui/index.html).
//! - „hud“: das Dashboard, ein normales Fenster mit Orb, Gespräch, Quellen und Anzeigen
//!   (ui/hud.html). Es entsteht erst, wenn es gebraucht wird, und verschwindet beim Schließen
//!   wieder ganz – solange es offen ist, ruht das Overlay.
//!
//! Die Fenster sind nur die Hülle. Die Darstellung (../ui) verbindet sich per WebSocket mit Jarvis
//! (ws://127.0.0.1:ORB_PORT). Bedient wird die App über das Tray-Symbol und ein Tastenkürzel
//! (Standard Cmd/Strg+Umschalt+J, JARVIS_HUD_SHORTCUT) zum Wechsel zwischen Orb und Dashboard.
//!
//! Von Jarvis gestartet (JARVIS_ORB_CHILD) kommen Port und Token als Umgebungsvariablen, und
//! stdin ist die Lebensader: endet Jarvis, liefert stdin EOF und die App beendet sich. Über
//! dieselbe Leitung schickt Jarvis Befehle, je eine Zeile: „hud“ öffnet das Dashboard, „overlay“
//! schließt es. Selbst gestartet liest die App das Token aus data/orb.token im Jarvis-Ordner.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::BufRead;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{
    AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
    WindowEvent,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

const DEFAULT_SHORTCUT: &str = "CmdOrCtrl+Shift+J";
const HUD_SIZE: (f64, f64) = (1440.0, 900.0);
const HUD_MIN_SIZE: (f64, f64) = (960.0, 620.0);

/// Was alle Fenster brauchen und was der Nutzer über das Tray eingestellt hat.
struct Ui {
    /// Setzt Port und Token im Fenster, bevor die Oberfläche lädt.
    init_script: String,
    /// Soll der Orb zu sehen sein, wenn kein Dashboard offen ist? (Tray: „Orb ausblenden“)
    orb_wanted: AtomicBool,
}

fn main() {
    let port: u16 = std::env::var("ORB_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8765);
    let token = read_token();
    let init_script = format!("window.ORB_PORT = {port}; window.ORB_TOKEN = \"{token}\";");

    tauri::Builder::default()
        .manage(Ui {
            init_script: init_script.clone(),
            orb_wanted: AtomicBool::new(true),
        })
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        toggle_hud(app);
                    }
                })
                .build(),
        )
        .setup(move |app| {
            // Kein Dock-Symbol und kein App-Menü – ohne Dashboard lebt die App nur im Tray
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            if std::env::var_os("JARVIS_ORB_CHILD").is_some() {
                let handle = app.handle().clone();
                std::thread::spawn(move || {
                    let stdin = std::io::stdin();
                    let mut line = String::new();
                    loop {
                        line.clear();
                        match stdin.lock().read_line(&mut line) {
                            Ok(0) | Err(_) => break, // Jarvis ist weg
                            Ok(_) => match line.trim() {
                                "hud" => show_hud(&handle),
                                "overlay" => close_hud(&handle),
                                _ => {}
                            },
                        }
                    }
                    handle.exit(0);
                });
            }

            let window = WebviewWindowBuilder::new(app, "orb", WebviewUrl::App("index.html".into()))
                .title("Jarvis Orb")
                .transparent(true)
                .decorations(false)
                .shadow(false)
                .always_on_top(true)
                .skip_taskbar(true)
                .resizable(false)
                .focused(false)
                .visible(false)
                .visible_on_all_workspaces(true)
                .initialization_script(&init_script)
                .build()?;
            cover_work_area(&window)?;
            window.set_ignore_cursor_events(true)?;
            window.show()?;

            // Monitore können sich ändern (an-/abgesteckt, Hauptbildschirm gewechselt, Ruhezustand).
            // macOS schiebt das Fenster dann woandershin und behält die alte Größe – der Orb säße
            // mitten im Bild und wäre zu groß. Deshalb regelmäßig nachziehen.
            std::thread::spawn(move || loop {
                std::thread::sleep(Duration::from_secs(2));
                let _ = cover_work_area(&window);
            });

            let hud = MenuItem::with_id(app, "hud", "Dashboard öffnen / schließen", true, None::<&str>)?;
            let toggle = MenuItem::with_id(app, "toggle", "Orb ausblenden", true, None::<&str>)?;
            let corner = MenuItem::with_id(app, "corner", "Ecke wechseln", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Beenden", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&hud, &toggle, &corner, &quit])?;

            let mut tray = TrayIconBuilder::with_id("orb-tray")
                .tooltip("Jarvis")
                .menu(&menu)
                .on_menu_event(move |app, event| match event.id.as_ref() {
                    "hud" => toggle_hud(app),
                    "toggle" => {
                        let ui = app.state::<Ui>();
                        let wanted = !ui.orb_wanted.load(Ordering::Relaxed);
                        ui.orb_wanted.store(wanted, Ordering::Relaxed);
                        let _ = toggle.set_text(if wanted { "Orb ausblenden" } else { "Orb einblenden" });
                        sync_orb(app);
                    }
                    "corner" => {
                        let _ = app.emit("cycle-corner", ());
                    }
                    "quit" => app.exit(0),
                    _ => {}
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;

            register_shortcut(app.handle());
            if std::env::var_os("JARVIS_HUD").is_some() {
                show_hud(app.handle());
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("Jarvis Orb konnte nicht gestartet werden");
}

/// Tastenkürzel Orb ↔ Dashboard. JARVIS_HUD_SHORTCUT leer = keins. Ist es schon vergeben oder
/// nicht lesbar, läuft die App ohne weiter (der Grund steht in data/orb.log).
fn register_shortcut(app: &AppHandle) {
    let wanted = std::env::var("JARVIS_HUD_SHORTCUT").unwrap_or_else(|_| DEFAULT_SHORTCUT.into());
    let wanted = wanted.trim();
    if wanted.is_empty() {
        return;
    }
    match wanted.parse::<Shortcut>() {
        Ok(shortcut) => {
            if let Err(e) = app.global_shortcut().register(shortcut) {
                eprintln!("Tastenkürzel {wanted} nicht registriert: {e}");
            }
        }
        Err(e) => eprintln!("Tastenkürzel {wanted} nicht verstanden: {e}"),
    }
}

fn toggle_hud(app: &AppHandle) {
    if app.get_webview_window("hud").is_some() {
        close_hud(app);
    } else {
        show_hud(app);
    }
}

/// Zeigt das Dashboard (holt ein vorhandenes nach vorn) und lässt den Orb solange ruhen.
fn show_hud(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("hud") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    // In einem eigenen Thread: Unter Windows blockiert das Anlegen eines Fensters, wenn es aus
    // einem Ereignis-Handler (Tray, Tastenkürzel) heraus geschieht.
    let app = app.clone();
    std::thread::spawn(move || {
        if app.get_webview_window("hud").is_some() {
            return; // doppelt ausgelöst
        }
        let (width, height) = hud_size(&app);
        let built = WebviewWindowBuilder::new(&app, "hud", WebviewUrl::App("hud.html".into()))
            .title("Jarvis")
            .inner_size(width, height)
            .min_inner_size(HUD_MIN_SIZE.0.min(width), HUD_MIN_SIZE.1.min(height))
            .center()
            .initialization_script(&app.state::<Ui>().init_script)
            .build();
        match built {
            Ok(window) => {
                // Mit Dashboard verhält sich die App wie ein normales Programm (Dock, Cmd+Tab)
                #[cfg(target_os = "macos")]
                let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
                let handle = app.clone();
                window.on_window_event(move |event| {
                    if let WindowEvent::Destroyed = event {
                        #[cfg(target_os = "macos")]
                        let _ = handle.set_activation_policy(tauri::ActivationPolicy::Accessory);
                        sync_orb(&handle);
                    }
                });
                let _ = window.set_focus();
                sync_orb(&app);
            }
            Err(e) => eprintln!("Dashboard-Fenster nicht geöffnet: {e}"),
        }
    });
}

fn close_hud(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("hud") {
        let _ = window.close(); // löst Destroyed aus → der Orb kommt zurück
    }
}

/// Der Orb ist zu sehen, wenn er gewünscht ist und kein Dashboard offen ist (das zeigt ihn selbst).
fn sync_orb(app: &AppHandle) {
    let Some(orb) = app.get_webview_window("orb") else {
        return;
    };
    let wanted = app.state::<Ui>().orb_wanted.load(Ordering::Relaxed);
    let _ = if wanted && app.get_webview_window("hud").is_none() {
        orb.show()
    } else {
        orb.hide()
    };
}

/// Größe des Dashboards: die Wunschgröße, aber nie größer als der nutzbare Bildschirm.
fn hud_size(app: &AppHandle) -> (f64, f64) {
    let monitor = app
        .get_webview_window("orb")
        .and_then(|w| w.primary_monitor().ok().flatten());
    let Some(monitor) = monitor else {
        return HUD_SIZE;
    };
    let area: LogicalSize<f64> = monitor.work_area().size.to_logical(monitor.scale_factor());
    (HUD_SIZE.0.min(area.width * 0.94), HUD_SIZE.1.min(area.height * 0.92))
}

/// Legt das Fenster über den nutzbaren Bereich des Hauptbildschirms (ohne Dock/Taskleiste).
/// Passt es schon, passiert nichts.
///
/// Gerechnet wird in logischen Punkten: Physische Pixel würden mit dem Skalierungsfaktor des
/// Bildschirms umgerechnet, auf dem das Fenster gerade liegt – bei Retina + externem Monitor
/// (Faktor 2 vs. 1) wird das Fenster sonst doppelt so groß.
fn cover_work_area(window: &WebviewWindow) -> tauri::Result<()> {
    let Some(monitor) = window.primary_monitor()? else {
        return Ok(());
    };
    let area = monitor.work_area();
    let pos: LogicalPosition<f64> = area.position.to_logical(monitor.scale_factor());
    let size: LogicalSize<f64> = area.size.to_logical(monitor.scale_factor());
    let scale = window.scale_factor()?;
    let cur_pos: LogicalPosition<f64> = window.outer_position()?.to_logical(scale);
    let cur_size: LogicalSize<f64> = window.outer_size()?.to_logical(scale);
    if (cur_pos.x - pos.x).abs() > 1.0 || (cur_pos.y - pos.y).abs() > 1.0 {
        window.set_position(pos)?;
    }
    if (cur_size.width - size.width).abs() > 1.0 || (cur_size.height - size.height).abs() > 1.0 {
        window.set_size(size)?;
    }
    Ok(())
}

/// Token für den Jarvis-Server: aus ORB_TOKEN oder aus data/orb.token neben dem Repo
/// (Binary liegt unter orb/src-tauri/target/<profil>/). Nur [A-Za-z0-9_-], damit es gefahrlos
/// ins Initialisierungsskript passt.
fn read_token() -> String {
    let raw = std::env::var("ORB_TOKEN").ok().or_else(|| {
        let file: PathBuf = std::env::current_exe().ok()?.ancestors().nth(5)?.join("data").join("orb.token");
        std::fs::read_to_string(file).ok()
    });
    raw.unwrap_or_default()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .collect()
}
