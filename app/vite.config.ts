import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// The dev server's React refresh needs one inline script, so only `vite dev` relaxes script-src; builds keep it strict.
function devCsp(): Plugin {
  return {
    name: "crewbots-dev-csp",
    apply: "serve",
    transformIndexHtml: (html) => html.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'"),
  };
}

// base "./" so the built files load from file:// inside Electron.
export default defineConfig({ plugins: [react(), devCsp()], base: "./" });
