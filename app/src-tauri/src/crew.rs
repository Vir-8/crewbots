//! The window's only way to the crew. This side finds the crew's daemon, or starts one, holds its token, makes every
//! request for the window, and relays /v1/live as events. The window never sees the token and can't reach the
//! network itself (its CSP allows nothing but IPC).

use std::collections::VecDeque;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;
use tokio_tungstenite::tungstenite::Message;

const START_TIMEOUT: Duration = Duration::from_secs(30);
const RECONNECT_AFTER: Duration = Duration::from_secs(2);
const LOG_LINES: usize = 40;

#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConnectionStatus {
    pub state: String,
    pub home: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub owns_daemon: Option<bool>,
}

#[derive(Clone, Debug, Deserialize)]
struct Daemon {
    url: String,
    token: String,
}

pub struct Crew {
    home: PathBuf,
    project: PathBuf,
    port: u16,
    http: reqwest::Client,
    daemon: Mutex<Option<Daemon>>,
    status: Mutex<ConnectionStatus>,
    child: Mutex<Option<Child>>,
    log: Arc<Mutex<VecDeque<String>>>,
}

impl Crew {
    pub fn from_env() -> Self {
        let home_dir = PathBuf::from(std::env::var("HOME").unwrap_or_default());
        let home = std::env::var("CREWBOTS_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|_| home_dir.join(".crewbots").join("crew"));
        // In development the app lives inside the crewbots checkout, which is where `uv run crewbots` works.
        let project = std::env::var("CREWBOTS_PROJECT")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join(".."));
        let port = std::env::var("CREWBOTS_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(8765);
        Crew {
            status: Mutex::new(ConnectionStatus {
                state: "connecting".into(),
                home: home.display().to_string(),
                ..Default::default()
            }),
            home,
            project,
            port,
            http: reqwest::Client::builder().timeout(Duration::from_secs(60)).build().expect("an HTTP client"),
            daemon: Mutex::new(None),
            child: Mutex::new(None),
            log: Arc::new(Mutex::new(VecDeque::new())),
        }
    }

    pub async fn status(&self) -> ConnectionStatus {
        self.status.lock().await.clone()
    }

    async fn set_status(&self, app: &AppHandle, state: &str, message: Option<String>) {
        let owns = self.child.lock().await.is_some();
        let url = self.daemon.lock().await.as_ref().map(|d| d.url.clone());
        let status = ConnectionStatus {
            state: state.into(),
            home: self.home.display().to_string(),
            url,
            message,
            owns_daemon: Some(owns),
        };
        *self.status.lock().await = status.clone();
        let _ = app.emit("crew:status", status);
    }

    /// Connects, follows /v1/live until it drops, and reconnects, for as long as the app runs.
    pub async fn run(self: Arc<Self>, app: AppHandle) {
        loop {
            match self.ensure_daemon(&app).await {
                Some(daemon) => {
                    *self.daemon.lock().await = Some(daemon.clone());
                    self.set_status(&app, "connected", None).await;
                    self.follow(&app, &daemon).await;
                    self.set_status(&app, "reconnecting", Some("Lost the daemon; reconnecting…".into())).await;
                }
                None if self.status.lock().await.state == "no-crew" => return,
                None => {}
            }
            tokio::time::sleep(RECONNECT_AFTER).await;
        }
    }

    async fn ensure_daemon(&self, app: &AppHandle) -> Option<Daemon> {
        if let Some(daemon) = self.read_info() {
            if self.reachable(&daemon).await {
                return Some(daemon);
            }
        }
        if !self.home.join("bots").is_dir() {
            let message = format!("No crew at {}. Run `uv run crewbots init` first.", self.home.display());
            self.set_status(app, "no-crew", Some(message)).await;
            return None;
        }
        self.set_status(app, "starting", Some("Starting the crew's daemon…".into())).await;
        if let Err(error) = self.start_daemon().await {
            self.set_status(app, "error", Some(format!("Couldn't start the daemon: {error}"))).await;
            return None;
        }
        let deadline = tokio::time::Instant::now() + START_TIMEOUT;
        while tokio::time::Instant::now() < deadline {
            if let Some(daemon) = self.read_info() {
                if self.reachable(&daemon).await {
                    return Some(daemon);
                }
            }
            let exited = match self.child.lock().await.as_mut() {
                Some(child) => child.try_wait().ok().flatten().is_some(),
                None => true,
            };
            if exited {
                break;
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        let log: Vec<String> = self.log.lock().await.iter().cloned().collect();
        *self.child.lock().await = None;
        self.set_status(app, "error", Some(format!("The daemon didn't start.\n{}", log.join("\n")))).await;
        None
    }

    async fn start_daemon(&self) -> std::io::Result<()> {
        let mut child = self.child.lock().await;
        if child.as_mut().is_some_and(|c| c.try_wait().ok().flatten().is_none()) {
            return Ok(()); // already starting
        }
        let path = login_path();
        let mut spawned = Command::new(find_in(&path, "uv"))
            .args(["run", "--directory"])
            .arg(&self.project)
            .args(["crewbots", "--home"])
            .arg(&self.home)
            .args(["serve", "--port", &self.port.to_string()])
            .env("PATH", &path) // the daemon starts the claude and codex CLIs, so it needs the user's PATH
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        if let Some(out) = spawned.stdout.take() {
            keep_lines(out, self.log.clone());
        }
        if let Some(err) = spawned.stderr.take() {
            keep_lines(err, self.log.clone());
        }
        *child = Some(spawned);
        Ok(())
    }

    /// Stops the daemon if this app started it. SIGINT lets it mark running tasks interrupted, so they can resume.
    pub async fn stop_owned_daemon(&self) {
        if let Some(mut child) = self.child.lock().await.take() {
            if let Some(pid) = child.id() {
                unsafe { libc::kill(pid as i32, libc::SIGINT) };
            }
            let _ = tokio::time::timeout(Duration::from_secs(5), child.wait()).await;
        }
    }

    fn read_info(&self) -> Option<Daemon> {
        let text = std::fs::read_to_string(self.home.join("daemon.json")).ok()?;
        serde_json::from_str(&text).ok()
    }

    async fn reachable(&self, daemon: &Daemon) -> bool {
        self.http
            .get(format!("{}/v1/bots", daemon.url))
            .bearer_auth(&daemon.token)
            .timeout(Duration::from_secs(3))
            .send()
            .await
            .is_ok_and(|r| r.status().is_success())
    }

    async fn follow(&self, app: &AppHandle, daemon: &Daemon) {
        let after = match self.request("GET", "/events/cursor", None).await {
            Value::Object(r) if r.get("ok") == Some(&Value::Bool(true)) => r["data"]["id"].as_i64().unwrap_or(0),
            _ => 0,
        };
        let url = format!("{}/v1/live?after={after}&token={}", daemon.url.replacen("http", "ws", 1), daemon.token);
        let Ok((mut socket, _)) = tokio_tungstenite::connect_async(url).await else { return };
        let _ = app.emit("crew:resync", ()); // anything missed while disconnected
        while let Some(Ok(message)) = socket.next().await {
            if let Message::Text(text) = message {
                if let Ok(value) = serde_json::from_str::<Value>(&text) {
                    let _ = app.emit("crew:live", value);
                }
            }
        }
    }

    /// One API call for the window. Only app paths: /bot/ is for bots, with their own tokens.
    pub async fn request(&self, method: &str, path: &str, body: Option<Value>) -> Value {
        if !path.starts_with('/') || path.starts_with("/bot/") {
            return json!({ "ok": false, "status": 0, "data": { "detail": format!("not an app path: {path}") } });
        }
        let Some(daemon) = self.daemon.lock().await.clone() else {
            return json!({ "ok": false, "status": 0, "data": { "detail": "not connected to the daemon" } });
        };
        let Ok(method) = reqwest::Method::from_bytes(method.as_bytes()) else {
            return json!({ "ok": false, "status": 0, "data": { "detail": format!("bad method {method}") } });
        };
        let mut request = self.http.request(method, format!("{}/v1{path}", daemon.url)).bearer_auth(&daemon.token);
        if let Some(body) = body {
            request = request.json(&body);
        }
        match request.send().await {
            Ok(response) => {
                let status = response.status();
                let text = response.text().await.unwrap_or_default();
                let data = serde_json::from_str::<Value>(&text).unwrap_or_else(|_| json!({ "detail": text }));
                json!({ "ok": status.is_success(), "status": status.as_u16(), "data": data })
            }
            Err(error) => json!({ "ok": false, "status": 0, "data": { "detail": error.to_string() } }),
        }
    }
}

/// Keeps the last lines the daemon printed, to show if it fails to start.
fn keep_lines<R: tokio::io::AsyncRead + Send + Unpin + 'static>(stream: R, log: Arc<Mutex<VecDeque<String>>>) {
    tokio::spawn(async move {
        let mut lines = BufReader::new(stream).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            let mut log = log.lock().await;
            log.push_back(line);
            while log.len() > LOG_LINES {
                log.pop_front();
            }
        }
    });
}

/// PATH as a login shell would have it. Apps started from Finder get a bare one, without Homebrew or ~/.local/bin.
fn login_path() -> String {
    let home = std::env::var("HOME").unwrap_or_default();
    let mut dirs: Vec<String> = std::env::var("PATH").unwrap_or_default().split(':').map(String::from).collect();
    for extra in [
        format!("{home}/.local/bin"),
        format!("{home}/.cargo/bin"),
        "/opt/homebrew/bin".into(),
        "/usr/local/bin".into(),
        "/usr/bin".into(),
        "/bin".into(),
    ] {
        if !dirs.contains(&extra) {
            dirs.push(extra);
        }
    }
    dirs.retain(|d| !d.is_empty());
    dirs.join(":")
}

fn find_in(path: &str, program: &str) -> PathBuf {
    path.split(':')
        .map(|dir| PathBuf::from(dir).join(program))
        .find(|candidate| candidate.is_file())
        .unwrap_or_else(|| PathBuf::from(program))
}
