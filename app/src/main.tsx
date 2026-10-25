import { invoke } from "@tauri-apps/api/core";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { inTauri } from "./bridge";
import { selftest } from "./selftest";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

if (inTauri) invoke<boolean>("selftest_wanted").then((wanted) => {
  if (wanted) selftest();
});
