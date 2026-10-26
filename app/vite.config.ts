/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The CSP is set by Tauri (src-tauri/tauri.conf.json), which also loads the built files.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 5173, strictPort: true },
  build: { target: "safari16" }, // WKWebView on macOS
  // `npm test`: the UI's logic and render times, in Node, with no browser.
  test: { include: ["src/**/*.test.{ts,tsx}"], environment: "node", setupFiles: ["src/test-setup.ts"] },
});
