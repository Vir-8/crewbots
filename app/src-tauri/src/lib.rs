mod crew;

use std::sync::Arc;

use serde_json::Value;
use tauri::{Manager, RunEvent, State};

use crew::{ConnectionStatus, Crew};

#[tauri::command]
async fn crew_status(crew: State<'_, Arc<Crew>>) -> Result<ConnectionStatus, ()> {
    Ok(crew.status().await)
}

#[tauri::command]
async fn crew_request(crew: State<'_, Arc<Crew>>, method: String, path: String, body: Option<Value>) -> Result<Value, ()> {
    Ok(crew.request(&method, &path, body).await)
}

/// For checking the app without someone at the screen: with CREWBOTS_SELFTEST set, the window runs its checks,
/// reports here, and the app prints the report and quits.
#[tauri::command]
fn selftest_wanted() -> bool {
    std::env::var("CREWBOTS_SELFTEST").is_ok()
}

#[tauri::command]
fn selftest_report(app: tauri::AppHandle, report: Value) {
    println!("CREWBOTS_SELFTEST {report}");
    app.exit(0);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let crew = Arc::new(Crew::from_env());
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(crew.clone())
        .invoke_handler(tauri::generate_handler![crew_status, crew_request, selftest_wanted, selftest_report])
        .setup(move |app| {
            tauri::async_runtime::spawn(crew.clone().run(app.handle().clone()));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the crewbots app")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                let crew = app.state::<Arc<Crew>>().inner().clone();
                tauri::async_runtime::block_on(crew.stop_owned_daemon());
            }
        });
}
