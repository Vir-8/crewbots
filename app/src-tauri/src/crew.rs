//! The window's only way to the crew. This side finds the crew's daemon, or starts it (the login service when it's
//! installed, a child of the app otherwise), holds its token, makes every request for the window, and relays /v1/live
//! as events. The window never sees the token and can't reach the network itself (its CSP allows nothing but IPC).
//! It also keeps the crew view the menu bar and notifications use.

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

use crate::watch::CrewView;

const SERVICE_LABEL: &str = "app.crewbots.daemon";

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
    label: String,
    pub view: Mutex<CrewView>,
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
            label: std::env::var("CREWBOTS_SERVICE_LABEL").unwrap_or_else(|_| SERVICE_LABEL.into()),
            view: Mutex::new(CrewView::default()),
        }
    }

    pub fn home(&self) -> &std::path::Path {
        &self.home
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
        // The login service, when it's installed, is the crew's daemon: start that rather than one of our own.
        let via_service = self.service_installed();
        let started = if via_service {
            self.set_status(app, "starting", Some("Starting the crew's login service…".into())).await;
            self.launchctl(&["kickstart", &format!("gui/{}/{}", unsafe { libc::getuid() }, self.label)]).await
        } else {
            self.set_status(app, "starting", Some("Starting the crew's daemon…".into())).await;
            self.start_daemon().await
        };
        if let Err(error) = started {
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
            let exited = !via_service
                && match self.child.lock().await.as_mut() {
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

    pub fn service_installed(&self) -> bool {
        let home = PathBuf::from(std::env::var("HOME").unwrap_or_default());
        home.join("Library/LaunchAgents").join(format!("{}.plist", self.label)).exists()
    }

    async fn launchctl(&self, args: &[&str]) -> std::io::Result<()> {
        let status = Command::new("/bin/launchctl").args(args).stdin(Stdio::null()).status().await?;
        if status.success() { Ok(()) } else { Err(std::io::Error::other(format!("launchctl {} failed", args.join(" ")))) }
    }

    /// `crewbots service <action>`: install or uninstall the login service, or ask about it. Installing while this
    /// app runs its own daemon hands over to the service: ours stops first, so the two never fight over the port.
    pub async fn service(&self, action: &str) -> Value {
        if action == "install" {
            self.stop_owned_daemon().await;
        }
        let path = login_path();
        let output = Command::new(find_in(&path, "uv"))
            .args(["run", "--directory"])
            .arg(&self.project)
            .args(["crewbots", "--home"])
            .arg(&self.home)
            .args(["service", action, "--label", &self.label, "--port", &self.port.to_string(), "--json"])
            .env("PATH", &path)
            .stdin(Stdio::null())
            .output()
            .await;
        match output {
            Ok(out) if out.status.success() => serde_json::from_slice(&out.stdout)
                .unwrap_or_else(|_| json!({ "error": String::from_utf8_lossy(&out.stdout).to_string() })),
            Ok(out) => json!({ "error": String::from_utf8_lossy(&out.stderr).trim().to_string() }),
            Err(error) => json!({ "error": error.to_string() }),
        }
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
        self.load_view(app).await;
        while let Some(Ok(message)) = socket.next().await {
            if let Message::Text(text) = message {
                if let Ok(value) = serde_json::from_str::<Value>(&text) {
                    self.watch(app, &value).await;
                    let _ = app.emit("crew:live", value);
                }
            }
        }
    }

    async fn load_view(&self, app: &AppHandle) {
        let tasks = self.request("GET", "/tasks?limit=1000", None).await;
        let crew = self.request("GET", "/crew", None).await;
        let rooms = self.request("GET", "/rooms", None).await;
        let chats = self.request("GET", "/chats", None).await;
        let list = tasks["data"].as_array().cloned().unwrap_or_default();
        let paused = crew["data"]["paused"].as_bool().unwrap_or(false);
        let summary = {
            let mut view = self.view.lock().await;
            view.load(&list, paused);
            view.set_rooms(rooms["data"].as_array().map(Vec::as_slice).unwrap_or_default());
            view.set_chats(&chats);
            view.summary()
        };
        crate::show_summary(app, &summary);
    }

    /// Keeps the menu bar current, and notifies on what needs you.
    async fn watch(&self, app: &AppHandle, message: &Value) {
        let (alerts, summary) = {
            let mut view = self.view.lock().await;
            let mut alerts = Vec::new();
            match message["type"].as_str() {
                Some("task") => alerts.extend(view.update(&message["task"])),
                Some("room_message") => alerts.extend(view.said(&message["message"])),
                Some("room") => view.set_rooms(std::slice::from_ref(&message["room"])),
                Some("chat_mute") => if let (Some(bot), Some(muted)) = (message["bot"].as_str(), message["muted"].as_bool()) { view.set_chat_mute(bot, muted); },
                Some("event") => alerts.extend(view.raised(&message["event"])),
                Some("crew") => view.set_paused(message["crew"]["paused"].as_bool().unwrap_or(false)),
                _ => {}
            }
            (alerts, view.summary())
        };
        crate::show_summary(app, &summary);
        for alert in alerts {
            crate::notify(app, &alert);
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
