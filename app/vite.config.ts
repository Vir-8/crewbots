/// <reference types="vitest/config" />
import { execSync } from "node:child_process";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The commit the app is built from, so it can tell what an update would bring (Settings, Updates).
const commit = (() => {
  try {
    return execSync("git rev-parse --short=12 HEAD", { stdio: ["ignore", "pipe", "ignore"] }).toString().trim();
  } catch {
    return "";
  }
})();

// The CSP is set by Tauri (src-tauri/tauri.conf.json), which also loads the built files.
export default defineConfig({
  plugins: [react()],
  define: { __APP_COMMIT__: JSON.stringify(commit) },
  clearScreen: false,
  server: { port: 5173, strictPort: true },
  build: { target: "safari16" }, // WKWebView on macOS
  // `npm test`: the UI's logic and render times, in Node, with no browser.
  test: { include: ["src/**/*.test.{ts,tsx}"], environment: "node", setupFiles: ["src/test-setup.ts"] },
});
