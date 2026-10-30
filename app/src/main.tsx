import { invoke } from "@tauri-apps/api/core";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/tokens.css";
import "./styles/base.css";
import { App } from "./App";
import { inTauri } from "./bridge";
import { Boundary, report } from "./components/Boundary";
import { selftest } from "./selftest";
import { Shown } from "./state";
import "./styles/selects.css"; // last: it applies over each select's own background

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Boundary where="the window" className="connecting">
      <App />
    </Boundary>
  </StrictMode>,
);

// What fails outside rendering (a handler, a promise nobody waited for) goes to the daemon's log too; a refused action
// was shown to you and isn't a crash.
window.addEventListener("error", (e) => report("the window", e.error ?? e.message));
window.addEventListener("unhandledrejection", (e) => e.reason instanceof Shown || report("a promise", e.reason));

if (inTauri) invoke<boolean>("selftest_wanted").then((wanted) => {
  if (wanted) selftest();
});
