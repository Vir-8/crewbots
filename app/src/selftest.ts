// Run when the app starts with CREWBOTS_SELFTEST set: checks the shell, the API and every layout end to end, then
// reports to the shell, which prints the report and quits. No one has to watch the window.

import { invoke } from "@tauri-apps/api/core";
import { api, bridge } from "./api";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function until<T>(check: () => T | undefined | false | null | Promise<T | undefined | false | null>, ms: number) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const value = await check();
    if (value) return value;
    await sleep(150);
  }
  return undefined;
}

export async function selftest() {
  const report: Record<string, unknown> = {};
  try {
    const status = await until(async () => {
      const s = await bridge.status();
      return s.state === "connected" ? s : undefined;
    }, 45_000);
    report.connected = Boolean(status);
    report.ownsDaemon = status?.ownsDaemon;
    report.bots = (await api.bots()).map((b) => b.name);
    report.tasks = (await api.tasks()).length;
    report.botPathRefused = !(await bridge.request("GET", "/bot/tasks")).ok;

    for (const [layout, selector] of [["Conversation", ".rail-goal"], ["Board", ".col"], ["Inbox", ".feed-item"]]) {
      const button = await until(() => [...document.querySelectorAll(".switcher button")].find((b) => b.textContent === layout), 10_000);
      (button as HTMLButtonElement | undefined)?.click();
      report[`layout:${layout}`] = (await until(() => document.querySelectorAll(selector).length, 10_000)) ?? 0;
    }

    // Live updates: start a task and stop it straight away, and see its events arrive through /v1/live.
    const seen: string[] = [];
    const off = bridge.onLive((message) => {
      if (message.type === "event") seen.push(`${message.event.task_id}:${message.event.kind}`);
    });
    const task = await api.newTask("reviewer", "Self-test from the app: reply with the single word ok.");
    await api.cancel(task.id);
    report.liveEvents = (await until(() => seen.filter((s) => s.startsWith(task.id)).length, 10_000)) ?? 0;
    report.cancelled = (await api.tasks()).find((t) => t.id === task.id)?.status;
    off();
  } catch (error) {
    report.error = String(error);
  }
  await invoke("selftest_report", { report });
}
