mod crew;
mod dictation;
mod watch;

use std::sync::{Arc, Mutex};

use serde_json::{Value, json};
use tauri::menu::{CheckMenuItem, Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, State, WindowEvent, Wry};
use tauri_plugin_notification::NotificationExt;

use crew::{ConnectionStatus, Crew};
use watch::{Alert, Summary};

const TRAY: &str = "main";

/// The menu bar item's live parts: the status line, the pause toggle, and start-at-login.
struct TrayItems {
    status: MenuItem<Wry>,
    pause: MenuItem<Wry>,
    login: CheckMenuItem<Wry>,
    summary: Mutex<Summary>,
}

#[tauri::command]
async fn crew_status(crew: State<'_, Arc<Crew>>) -> Result<ConnectionStatus, ()> {
    Ok(crew.status().await)
}

#[tauri::command]
async fn crew_request(crew: State<'_, Arc<Crew>>, method: String, path: String, body: Option<Value>) -> Result<Value, ()> {
    Ok(crew.request(&method, &path, body).await)
}

/// What the menu bar shows: for the self-test, which can't look at the menu bar.
#[tauri::command]
fn menu_bar(items: State<'_, TrayItems>) -> Value {
    let summary = items.summary.lock().unwrap().clone();
    json!({ "title": summary.title(), "line": summary.line(), "paused": summary.paused })
}

/// For checking the app without someone at the screen: with CREWBOTS_SELFTEST set, the window runs its checks,
/// reports here, and the app prints the report and quits.
#[tauri::command]
fn selftest_wanted() -> bool {
    std::env::var("CREWBOTS_SELFTEST").is_ok()
}

#[tauri::command]
fn selftest_report(app: AppHandle, report: Value) {
    println!("CREWBOTS_SELFTEST {report}");
    app.exit(0);
}

/// Called by the crew view whenever it changes.
pub(crate) fn show_summary(app: &AppHandle, summary: &Summary) {
    if let Some(tray) = app.tray_by_id(TRAY) {
        let _ = tray.set_title(Some(summary.title()));
        let _ = tray.set_tooltip(Some(format!("crewbots: {}", summary.line())));
    }
    if let Some(items) = app.try_state::<TrayItems>() {
        let _ = items.status.set_text(summary.line());
        let _ = items.pause.set_text(if summary.paused { "Resume the crew" } else { "Pause the crew" });
        *items.summary.lock().unwrap() = summary.clone();
    }
}

/// A native notification, unless you're already looking at the app.
pub(crate) fn notify(app: &AppHandle, alert: &Alert) {
    let looking = app.get_webview_window("main").is_some_and(|w| w.is_focused().unwrap_or(false));
    if looking {
        return;
    }
    let (title, body) = alert.text();
    let _ = app.notification().builder().title(title).body(body).show();
}

fn show_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn on_menu(app: &AppHandle, event: MenuEvent) {
    let crew = app.state::<Arc<Crew>>().inner().clone();
    match event.id.as_ref() {
        "open" => show_window(app),
        "pause" => {
            let paused = app.state::<TrayItems>().summary.lock().unwrap().paused;
            tauri::async_runtime::spawn(async move {
                crew.request("POST", if paused { "/crew/resume" } else { "/crew/pause" }, None).await;
            });
        }
        "login" => {
            let app = app.clone();
            tauri::async_runtime::spawn(async move {
                let action = if crew.service_installed() { "uninstall" } else { "install" };
                let result = crew.service(action).await;
                let installed = result["installed"].as_bool().unwrap_or(crew.service_installed());
                if let Some(items) = app.try_state::<TrayItems>() {
                    let _ = items.login.set_checked(installed);
                }
                if let Some(error) = result["error"].as_str() {
                    let _ = app.notification().builder().title("Couldn't change start at login").body(error).show();
                }
            });
        }
        "quit" => app.exit(0),
        _ => {}
    }
}

fn build_tray(app: &tauri::App, crew: &Crew) -> tauri::Result<()> {
    let status = MenuItem::with_id(app, "status", "Connecting…", false, None::<&str>)?;
    let open = MenuItem::with_id(app, "open", "Open crewbots", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", "Pause the crew", true, None::<&str>)?;
    let login = CheckMenuItem::with_id(app, "login", "Start at login", true, crew.service_installed(), None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit crewbots", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[
        &status,
        &PredefinedMenuItem::separator(app)?,
        &open,
        &pause,
        &login,
        &PredefinedMenuItem::separator(app)?,
        &quit,
    ])?;
    let mut tray = TrayIconBuilder::with_id(TRAY).menu(&menu).show_menu_on_left_click(true).tooltip("crewbots");
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.on_menu_event(on_menu).build(app)?;
    app.manage(TrayItems { status, pause, login, summary: Mutex::new(Summary::default()) });
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let crew = Arc::new(Crew::from_env());
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .manage(crew.clone())
        .manage(dictation::Dictation::default())
        .invoke_handler(tauri::generate_handler![
            crew_status,
            crew_request,
            menu_bar,
            selftest_wanted,
            selftest_report,
            dictation::dictation_start,
            dictation::dictation_stop
        ])
        .setup(move |app| {
            build_tray(app, &crew)?;
            tauri::async_runtime::spawn(crew.clone().run(app.handle().clone()));
            Ok(())
        })
        // Closing the window leaves crewbots in the menu bar; Quit is in its menu.
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building the crewbots app")
        .run(|app, event| match event {
            RunEvent::Reopen { .. } => show_window(app), // clicking the dock icon
            RunEvent::Exit => {
                let crew = app.state::<Arc<Crew>>().inner().clone();
                tauri::async_runtime::block_on(crew.stop_owned_daemon()); // never the login service
            }
            _ => {}
        });
}
