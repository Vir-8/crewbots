// The desktop shell. It finds the crew's daemon, or starts one, and is the only part of the app that holds the
// daemon's token: the window asks for everything over IPC and never sees it. Live updates come from /live here and
// are passed on to the window.

const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const HOME = process.env.CREWBOTS_HOME || path.join(os.homedir(), ".crewbots", "crew");
const PROJECT = process.env.CREWBOTS_PROJECT || path.resolve(__dirname, "..", ".."); // where `uv run crewbots` works
const PORT = Number(process.env.CREWBOTS_PORT || 8765);
const DEV_URL = process.env.CREWBOTS_DEV_URL || "http://localhost:5173";
const START_TIMEOUT_MS = 30_000;
const RECONNECT_MS = 2_000;

let win = null;
let daemon = null; // { url, token }
let child = null; // the daemon process, when this app started it
let childLog = [];
let socket = null;
let status = { state: "connecting", home: HOME };

function setStatus(next) {
  status = { home: HOME, ...next };
  win?.webContents.send("crew:status", status);
}

function readInfo() {
  try {
    return JSON.parse(fs.readFileSync(path.join(HOME, "daemon.json"), "utf8"));
  } catch {
    return null;
  }
}

async function reachable(info) {
  try {
    const response = await fetch(`${info.url}/v1/bots`, { headers: { Authorization: `Bearer ${info.token}` } });
    return response.ok;
  } catch {
    return false;
  }
}

async function connect() {
  let info = readInfo();
  if (!info || !(await reachable(info))) {
    if (!fs.existsSync(path.join(HOME, "bots"))) {
      setStatus({ state: "no-crew", message: `No crew at ${HOME}. Run \`uv run crewbots init\` first.` });
      return;
    }
    info = await startDaemon();
    if (!info) return;
  }
  daemon = info;
  setStatus({ state: "connected", url: info.url, ownsDaemon: Boolean(child) });
  await follow();
}

async function startDaemon() {
  setStatus({ state: "starting", message: "Starting the crew's daemon…" });
  childLog = [];
  child = spawn("uv", ["run", "--directory", PROJECT, "crewbots", "--home", HOME, "serve", "--port", String(PORT)], {
    stdio: ["ignore", "pipe", "pipe"],
  });
  const keep = (chunk) => (childLog = [...childLog, ...String(chunk).split("\n")].slice(-40));
  child.stdout.on("data", keep);
  child.stderr.on("data", keep);
  child.on("exit", (code) => {
    child = null;
    if (status.state === "connected") setStatus({ state: "error", message: `The daemon exited (code ${code}).` });
  });
  const deadline = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const info = readInfo();
    if (info && (await reachable(info))) return info;
    if (!child) break;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  setStatus({ state: "error", message: `The daemon didn't start.\n${childLog.join("\n")}` });
  return null;
}

async function request(method, pathname, body) {
  if (!daemon) return { ok: false, status: 0, data: { detail: "not connected to the daemon" } };
  if (typeof pathname !== "string" || !pathname.startsWith("/") || pathname.startsWith("/bot/")) {
    return { ok: false, status: 0, data: { detail: `not an app path: ${pathname}` } }; // /bot/ is for bots only
  }
  try {
    const response = await fetch(`${daemon.url}/v1${pathname}`, {
      method,
      headers: { Authorization: `Bearer ${daemon.token}`, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    return { ok: response.ok, status: response.status, data };
  } catch (error) {
    return { ok: false, status: 0, data: { detail: String(error) } };
  }
}

async function follow() {
  const cursor = await request("GET", "/events/cursor");
  const after = cursor.ok ? cursor.data.id : 0;
  const url = `${daemon.url.replace(/^http/, "ws")}/v1/live?after=${after}&token=${encodeURIComponent(daemon.token)}`;
  socket?.close();
  socket = new WebSocket(url);
  socket.onmessage = (message) => win?.webContents.send("crew:live", JSON.parse(String(message.data)));
  socket.onopen = () => win?.webContents.send("crew:resync"); // anything missed while disconnected
  socket.onclose = () => {
    if (app.isQuitting) return;
    setStatus({ state: "reconnecting", message: "Lost the daemon; reconnecting…" });
    setTimeout(connect, RECONNECT_MS);
  };
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 560,
    title: "crewbots",
    backgroundColor: "#111214",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  // The window only ever shows the app; links open in the browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(DEV_URL) && !url.startsWith("file://")) event.preventDefault();
  });

  const params = new URLSearchParams();
  if (process.env.CREWBOTS_OPEN_TASK) params.set("task", process.env.CREWBOTS_OPEN_TASK);
  if (process.env.CREWBOTS_OPEN_LAYOUT) params.set("layout", process.env.CREWBOTS_OPEN_LAYOUT);
  const query = params.size ? `?${params}` : "";
  if (app.isPackaged || process.env.CREWBOTS_USE_BUILD) {
    win.loadFile(path.join(__dirname, "..", "dist", "index.html"), { search: query.slice(1) });
  } else {
    win.loadURL(DEV_URL + "/" + query);
  }
  if (process.env.CREWBOTS_SCREENSHOT) screenshotWhenSettled(process.env.CREWBOTS_SCREENSHOT);
}

// For checking the UI without a person at the screen: CREWBOTS_SCREENSHOT=out.png writes the window to a file once it
// has settled, and CREWBOTS_SCREENSHOT_QUIT=1 quits afterwards.
function screenshotWhenSettled(file) {
  win.webContents.once("did-finish-load", () => {
    setTimeout(async () => {
      const image = await win.webContents.capturePage();
      fs.writeFileSync(file, image.toPNG());
      if (process.env.CREWBOTS_SCREENSHOT_QUIT) app.quit();
    }, Number(process.env.CREWBOTS_SCREENSHOT_DELAY_MS || 2500));
  });
}

ipcMain.handle("crew:status", () => status);
ipcMain.handle("crew:request", (_event, method, pathname, body) => request(method, pathname, body));
ipcMain.handle("crew:pickFolder", async () => {
  const result = await dialog.showOpenDialog(win, { properties: ["openDirectory"] });
  return result.canceled ? null : result.filePaths[0];
});
ipcMain.handle("crew:reveal", (_event, target) => {
  if (typeof target === "string" && fs.existsSync(target)) shell.showItemInFolder(target);
});

app.whenReady().then(() => {
  createWindow();
  win.webContents.once("did-finish-load", connect);
});

app.on("before-quit", () => {
  app.isQuitting = true;
  socket?.close();
  child?.kill("SIGINT"); // the daemon marks running tasks interrupted; a message resumes them
});

app.on("window-all-closed", () => app.quit());
