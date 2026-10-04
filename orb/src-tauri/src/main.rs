//! Jarvis Orb: transparentes, klick-durchlässiges Overlay über dem ganzen Bildschirm.
//!
//! Das Fenster selbst ist nur die Hülle. Die Darstellung (../ui) verbindet sich per WebSocket
//! mit Jarvis (ws://127.0.0.1:ORB_PORT) und zeichnet den Orb. Bedient wird die App über das
//! Tray-Symbol: Orb aus-/einblenden, Ecke für den Ruhezustand wechseln, beenden.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use std::time::Duration;
use tauri::{Emitter, LogicalPosition, LogicalSize, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

fn main() {
    let port: u16 = std::env::var("ORB_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8765);

    tauri::Builder::default()
        .setup(move |app| {
            // Kein Dock-Symbol und kein App-Menü – die App lebt nur im Tray
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

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
                .initialization_script(&format!("window.ORB_PORT = {port};"))
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

            let toggle = MenuItem::with_id(app, "toggle", "Orb ausblenden", true, None::<&str>)?;
            let corner = MenuItem::with_id(app, "corner", "Ecke wechseln", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Beenden", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&toggle, &corner, &quit])?;

            let mut tray = TrayIconBuilder::with_id("orb-tray")
                .tooltip("Jarvis Orb")
                .menu(&menu)
                .on_menu_event(move |app, event| match event.id.as_ref() {
                    "toggle" => {
                        if let Some(w) = app.get_webview_window("orb") {
                            let visible = w.is_visible().unwrap_or(true);
                            let _ = if visible { w.hide() } else { w.show() };
                            let _ = toggle.set_text(if visible { "Orb einblenden" } else { "Orb ausblenden" });
                        }
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
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("Jarvis Orb konnte nicht gestartet werden");
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
