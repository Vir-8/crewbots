//! Dictation: runs crewbots-dictate (dictation/main.swift) while you talk, and sends the window what it hears.
//! macOS asks once for the microphone and speech recognition, on the app's behalf.

use serde_json::Value;
use tauri::{AppHandle, Emitter, State};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{ChildStdin, Command};
use tokio::sync::Mutex;

#[derive(Default)]
pub struct Dictation {
    stdin: Mutex<Option<ChildStdin>>,
}

/// The helper sits beside the app's own binary: in the .app's Contents/MacOS, or in target/ while developing.
fn helper() -> std::io::Result<std::path::PathBuf> {
    let exe = std::env::current_exe()?;
    Ok(exe.with_file_name("crewbots-dictate"))
}

#[tauri::command]
pub async fn dictation_start(app: AppHandle, dictation: State<'_, Dictation>) -> Result<(), String> {
    let mut stdin = dictation.stdin.lock().await;
    if let Some(mut old) = stdin.take() {
        let _ = old.write_all(b"stop\n").await;
    }
    let mut child = Command::new(helper().map_err(|e| e.to_string())?)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .map_err(|e| format!("couldn't start dictation: {e}"))?;
    *stdin = child.stdin.take();
    let stdout = child.stdout.take().ok_or("dictation has no output")?;
    tauri::async_runtime::spawn(async move {
        let mut lines = BufReader::new(stdout).lines();
        let mut error: Option<String> = None;
        while let Ok(Some(line)) = lines.next_line().await {
            let Ok(message) = serde_json::from_str::<Value>(&line) else { continue };
            if let Some(text) = message["text"].as_str() {
                let _ = app.emit("dictation:heard", text);
            }
            if let Some(why) = message["error"].as_str() {
                error = Some(why.to_string());
            }
        }
        let _ = child.wait().await;
        let _ = app.emit("dictation:stopped", error);
    });
    Ok(())
}

#[tauri::command]
pub async fn dictation_stop(dictation: State<'_, Dictation>) -> Result<(), String> {
    if let Some(mut stdin) = dictation.stdin.lock().await.take() {
        let _ = stdin.write_all(b"stop\n").await;
    }
    Ok(())
}
