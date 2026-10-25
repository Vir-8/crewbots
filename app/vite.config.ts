import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The CSP is set by Tauri (src-tauri/tauri.conf.json), which also loads the built files.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 5173, strictPort: true },
  build: { target: "safari16" }, // WKWebView on macOS
});
