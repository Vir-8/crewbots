// The whole surface the window gets: calls go to the main process, which holds the daemon's token.

const { contextBridge, ipcRenderer } = require("electron");

function subscribe(channel, callback) {
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

contextBridge.exposeInMainWorld("crew", {
  status: () => ipcRenderer.invoke("crew:status"),
  request: (method, path, body) => ipcRenderer.invoke("crew:request", method, path, body),
  pickFolder: () => ipcRenderer.invoke("crew:pickFolder"),
  reveal: (target) => ipcRenderer.invoke("crew:reveal", target),
  onStatus: (callback) => subscribe("crew:status", callback),
  onLive: (callback) => subscribe("crew:live", callback),
  onResync: (callback) => subscribe("crew:resync", callback),
});
