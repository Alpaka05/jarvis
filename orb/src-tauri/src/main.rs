//! Jarvis Orb: transparentes, klick-durchlässiges Overlay über dem ganzen Bildschirm.
//!
//! Das Fenster selbst ist nur die Hülle. Die Darstellung (../ui) verbindet sich per WebSocket
//! mit Jarvis (ws://127.0.0.1:ORB_PORT) und zeichnet den Orb. Bedient wird die App über das
//! Tray-Symbol: Orb aus-/einblenden, Ecke für den Ruhezustand wechseln, beenden.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

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
fn cover_work_area(window: &WebviewWindow) -> tauri::Result<()> {
    if let Some(monitor) = window.primary_monitor()? {
        let area = monitor.work_area();
        window.set_position(area.position)?;
        window.set_size(area.size)?;
    }
    Ok(())
}
