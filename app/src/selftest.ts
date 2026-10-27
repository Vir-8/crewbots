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
    // Timings (A12's budgets): from the window starting to the first chat drawn, and to open another one.
    await until(() => document.querySelector(".thread-col"), 10_000);
    report.firstChatMs = Math.round(performance.now());
    const rows = [...document.querySelectorAll<HTMLElement>(".chat-row")];
    const other = rows.find((r) => !r.classList.contains("on")) ?? rows[0];
    const opening = performance.now();
    other?.click();
    report.chatOpen = Boolean(await until(() => document.querySelector(".chat-head-title")?.textContent, 10_000));
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    report.openChatMs = Math.round(performance.now() - opening);
    document.querySelector<HTMLElement>(".chat-head-who")?.click();
    report.profileOpened = Boolean(await until(() => document.querySelector(".panel.profile .profile-name"), 10_000));

    // The menu bar follows the crew: pausing shows there, through /v1/live. Nothing here starts work in your crew.
    const crewMessages: boolean[] = [];
    const off = bridge.onLive((message) => {
      if (message.type === "crew") crewMessages.push(message.crew.paused);
    });
    type MenuBar = { title: string; line: string; paused: boolean };
    const pausing = performance.now();
    const heard = until(() => (crewMessages.length ? performance.now() - pausing : 0), 10_000);
    await bridge.request("POST", "/crew/pause");
    report.liveMs = Math.round((await heard) ?? -1); // the daemon's word, through the shell, to the window
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
    report.files = await checkFiles();
  } catch (error) {
    report.error = String(error);
  }
  await invoke("selftest_report", { report });
}

const PDF = "%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n" +
  "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<<>>>>endobj\n" +
  "4 0 obj<</Length 28>>stream\n0 0 1 rg 20 20 160 160 re f\nendstream endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n";

/** Files as a chat draws them, straight from disk through the asset protocol: a picture, a video, a PDF's first page
 * from Quick Look, a text file; and a file outside the crew's attachments stays out of reach. The files are this
 * check's own attachments. */
async function checkFiles(): Promise<Record<string, unknown>> {
  const base64 = (bytes: ArrayBuffer) => {
    let binary = "";
    new Uint8Array(bytes).forEach((b) => (binary += String.fromCharCode(b)));
    return btoa(binary);
  };
  const drawn = (src: string) => new Promise<boolean>((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img.naturalWidth > 0);
    img.onerror = () => resolve(false);
    img.src = src;
  });
  const out: Record<string, unknown> = {};
  const step = async (name: string, check: () => Promise<unknown>) => {
    try {
      out[name] = await check();
    } catch (error) {
      out[name] = `failed: ${error}`;
    }
  };
  await step("image", async () => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 8;
    const png = canvas.toDataURL("image/png");
    const image = await api.uploadAttachment("selftest.png", png.slice(png.indexOf(",") + 1));
    return drawn(bridge.fileSrc(image.path));
  });
  await step("video", async () => {
    const recorded = await record();
    const video = await api.uploadAttachment(recorded.type.includes("mp4") ? "selftest.mp4" : "selftest.webm", base64(await recorded.arrayBuffer()));
    return new Promise<string>((resolve) => {
      const v = document.createElement("video");
      v.preload = "metadata";
      v.onloadedmetadata = () => resolve(`${v.videoWidth}x${v.videoHeight}`);
      v.onerror = () => resolve(`failed: ${v.error?.message ?? v.error?.code}`);
      v.src = bridge.fileSrc(video.path);
    });
  });
  await step("pdfPage", async () => {
    const pdf = await api.uploadAttachment("selftest.pdf", btoa(PDF));
    const started = performance.now();
    const thumb = await bridge.thumbnail(pdf.path, 200);
    out.pdfPageMs = Math.round(performance.now() - started);
    return thumb ? drawn(bridge.fileSrc(thumb)) : "no thumbnail";
  });
  await step("text", async () => {
    const text = await api.uploadAttachment("selftest.py", btoa("print('hi')\n"));
    return (await (await fetch(bridge.fileSrc(text.path))).text()) === "print('hi')\n";
  });
  await step("outsideRefused", async () => !(await fetch(bridge.fileSrc("/etc/hosts")).then((r) => r.ok, () => false)));
  return out;
}

/** A second of video, recorded from a canvas, to play back from disk. */
async function record(): Promise<Blob> {
  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 48;
  const g = canvas.getContext("2d")!;
  const recorder = new MediaRecorder(canvas.captureStream(15));
  const chunks: Blob[] = [];
  recorder.ondataavailable = (e) => chunks.push(e.data);
  const stopped = new Promise((resolve) => (recorder.onstop = resolve));
  recorder.start();
  for (let frame = 0; frame < 15; frame++) {
    g.fillStyle = `hsl(${frame * 24}, 70%, 50%)`;
    g.fillRect(0, 0, 64, 48);
    await sleep(66);
  }
  recorder.stop();
  await stopped;
  return new Blob(chunks, { type: recorder.mimeType });
}
