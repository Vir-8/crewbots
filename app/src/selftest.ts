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

    // The conversations: a chat with each bot and the group chats in the sidebar, and a chat's profile beside it.
    report.chats = (await until(() => document.querySelectorAll(".chat-row").length, 10_000)) ?? 0;
    document.querySelector<HTMLElement>(".chat-row")?.click();
    report.chatOpen = Boolean(await until(() => document.querySelector(".chat-head-title")?.textContent, 10_000));
    document.querySelector<HTMLElement>(".chat-head-who")?.click();
    report.profileOpened = Boolean(await until(() => document.querySelector(".panel.profile .profile-name"), 10_000));

    // The menu bar follows the crew: pausing shows there, through /v1/live. Nothing here starts work in your crew.
    const crewMessages: boolean[] = [];
    const off = bridge.onLive((message) => {
      if (message.type === "crew") crewMessages.push(message.crew.paused);
    });
    type MenuBar = { title: string; line: string; paused: boolean };
    await bridge.request("POST", "/crew/pause");
    const paused = await until(async () => {
      const bar = await invoke<MenuBar>("menu_bar");
      return bar.paused ? bar : undefined;
    }, 10_000);
    report.menuBarWhilePaused = paused?.line ?? "never showed paused";
    await bridge.request("POST", "/crew/resume");
    report.menuBarAfter = (await until(async () => {
      const bar = await invoke<MenuBar>("menu_bar");
      return bar.paused ? undefined : bar;
    }, 10_000))?.line;

    // Live updates reach the window too: the pause and resume above arrive as crew messages.
    report.liveCrewMessages = (await until(() => (crewMessages.length >= 2 ? crewMessages.length : 0), 10_000)) ?? 0;
    off();
  } catch (error) {
    report.error = String(error);
  }
  await invoke("selftest_report", { report });
}
