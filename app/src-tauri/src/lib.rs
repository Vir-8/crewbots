mod crew;
mod dictation;
mod files;
mod update;
mod watch;

use std::sync::{Arc, Mutex, OnceLock};
use std::time::SystemTime;

use serde_json::{Value, json};
use tauri::menu::{AboutMetadata, CheckMenuItem, Menu, MenuEvent, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, WindowEvent, Wry};
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

/// Whether the crew starts at login (the launchd service), and, given `enabled`, turns it on or off.
#[tauri::command]
async fn start_at_login(app: AppHandle, crew: State<'_, Arc<Crew>>, enabled: Option<bool>) -> Result<bool, String> {
    if let Some(want) = enabled
        && want != crew.service_installed()
    {
        let result = crew.service(if want { "install" } else { "uninstall" }).await;
        if let Some(error) = result["error"].as_str() {
            return Err(error.to_string());
        }
    }
    let installed = crew.service_installed();
    if let Some(items) = app.try_state::<TrayItems>() {
        let _ = items.login.set_checked(installed);
    }
    Ok(installed)
}

/// For checking the app without someone at the screen: with CREWBOTS_SELFTEST set, the window runs its checks,
/// reports here, and the app prints the report and quits.
/// When this app's executable was built, as it was when the app started. Installing a new build replaces the file.
static LAUNCHED_BUILD: OnceLock<Option<SystemTime>> = OnceLock::new();

fn executable_time() -> Option<SystemTime> {
    std::env::current_exe().ok().and_then(|path| std::fs::metadata(path).ok()).and_then(|m| m.modified().ok())
}

/// Whether a newer build is installed over the running one, so relaunching would update it.
#[tauri::command]
fn app_info() -> Value {
    let launched = *LAUNCHED_BUILD.get_or_init(executable_time);
    let installed_newer = matches!((launched, executable_time()), (Some(was), Some(now)) if now > was);
    json!({ "installed_newer": installed_newer })
}

/// Quits and opens the app again, from whatever build is installed now.
#[tauri::command]
fn restart_app(app: AppHandle) {
    app.restart();
}

#[tauri::command]
fn selftest_wanted() -> bool {
    std::env::var("CREWBOTS_SELFTEST").is_ok()
}

/// With CREWBOTS_SELFTEST=update, the self-test also installs an update the daemon has built, the way Settings does,
/// to see that macOS lets this app replace itself; it doesn't restart.
#[tauri::command]
fn selftest_update() -> bool {
    std::env::var("CREWBOTS_SELFTEST").is_ok_and(|v| v == "update")
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
        // The app menu's own: the window does them.
        "check-updates" | "settings" => {
            show_window(app);
            let _ = app.emit("app:menu", event.id.as_ref());
        }
        _ => {}
    }
}

/// The menu bar's menus: crewbots's own (About, Check for Updates…, Settings…, and the usual Hide and Quit), then
/// Edit, View and Window as every Mac app has them.
fn app_menu(app: &tauri::App) -> tauri::Result<Menu<Wry>> {
    let about = AboutMetadata {
        version: Some(format!("{} ({})", env!("CARGO_PKG_VERSION"), env!("CREWBOTS_COMMIT"))),
        ..Default::default()
    };
    let crewbots = Submenu::with_items(app, "crewbots", true, &[
        &PredefinedMenuItem::about(app, Some("About crewbots"), Some(about))?,
        &PredefinedMenuItem::separator(app)?,
        &MenuItem::with_id(app, "check-updates", "Check for Updates…", true, None::<&str>)?,
        &MenuItem::with_id(app, "settings", "Settings…", true, Some("CmdOrCtrl+,"))?,
        &PredefinedMenuItem::separator(app)?,
        &PredefinedMenuItem::services(app, None)?,
        &PredefinedMenuItem::separator(app)?,
        &PredefinedMenuItem::hide(app, Some("Hide crewbots"))?,
        &PredefinedMenuItem::hide_others(app, None)?,
        &PredefinedMenuItem::show_all(app, None)?,
        &PredefinedMenuItem::separator(app)?,
        &PredefinedMenuItem::quit(app, Some("Quit crewbots"))?,
    ])?;
    let edit = Submenu::with_items(app, "Edit", true, &[
        &PredefinedMenuItem::undo(app, None)?,
        &PredefinedMenuItem::redo(app, None)?,
        &PredefinedMenuItem::separator(app)?,
        &PredefinedMenuItem::cut(app, None)?,
        &PredefinedMenuItem::copy(app, None)?,
        &PredefinedMenuItem::paste(app, None)?,
        &PredefinedMenuItem::select_all(app, None)?,
    ])?;
    let view = Submenu::with_items(app, "View", true, &[&PredefinedMenuItem::fullscreen(app, None)?])?;
    let window = Submenu::with_items(app, "Window", true, &[
        &PredefinedMenuItem::minimize(app, None)?,
        &PredefinedMenuItem::maximize(app, Some("Zoom"))?,
        &PredefinedMenuItem::separator(app)?,
        &PredefinedMenuItem::close_window(app, None)?,
    ])?;
    Menu::with_items(app, &[&crewbots, &edit, &view, &window])
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
            selftest_update,
            selftest_report,
            start_at_login,
            app_info,
            restart_app,
            dictation::dictation_start,
            dictation::dictation_stop,
            files::show_files,
            files::open_file,
            files::thumbnail,
            update::install_update
        ])
        .setup(move |app| {
            LAUNCHED_BUILD.get_or_init(executable_time);
            app.set_menu(app_menu(app)?)?;
            build_tray(app, &crew)?;
            files::allow(app.handle(), crew.home());
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
