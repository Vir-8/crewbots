"""Manual daemon-path smoke check; never run by pytest. Needs the Vite preview on :5173."""

import argparse
import asyncio
import json
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from crewbots.browser import Browser
from crewbots.crew import Bot, Crew
from crewbots.crew_tools import CrewTools
from crewbots.runner import Runner
from crewbots.store import Store


def dump_diagnostics(browser, folder):
    diagnostics = browser.diagnostics()
    outputs = {
        "chrome.stderr.log": diagnostics.pop("chrome_stderr"),
        "proxy.json": json.dumps(diagnostics.pop("proxy"), indent=2),
        "cdp.json": json.dumps(diagnostics, indent=2),
    }
    for name, text in outputs.items():
        (folder / name).write_text(text)
    return outputs


async def main(url="http://localhost:5173"):
    folder = Path.cwd() / "browser-evidence"
    folder.mkdir(exist_ok=True)
    progress = folder / "progress.log"
    progress.write_text("")

    def report(text):
        line = f"{datetime.now(UTC).isoformat()} {text}"
        print(line, flush=True)
        with progress.open("a") as stream:
            stream.write(line + "\n")

    report(f"Python: {sys.executable}; version: {sys.version}; base: {sys.base_prefix}")
    report(f"Workspace: {Path.cwd()}; URL: {url}")
    parsed = urlsplit(url)
    try:
        addresses = sorted({str(info[4]) for info in socket.getaddrinfo(
            parsed.hostname, parsed.port or 80, type=socket.SOCK_STREAM)})
        report(f"Resolved preview addresses: {addresses}")
    except OSError as e:
        report(f"Preview DNS failed: {e}")

    store = Store(folder / "check.db")
    bot = Bot(name="browser-check", title="Browser check", description="Local smoke check", instructions="",
              backend="codex", workspace=folder, network=False)
    runner = Runner(store, Crew(home=folder, bots={bot.name: bot}, team_files={}))
    # Exercise the exact daemon service, using a throwaway profile beside the evidence.
    runner.browser = Browser(folder / "profiles")
    task = store.create_task(bot.name, "Check browser tools")
    crew = CrewTools(runner, task.id, False)
    try:
        report("Opening preview (launch Chrome, attach tab, configure interception, navigate)")
        report(str(await crew.call("browser_open", {"url": url})))
        for theme in ("light", "dark"):
            report(f"Capturing {theme} screenshot at 1400x860")
            result = await crew.call("browser_screenshot", {"width": 1400, "height": 860, "theme": theme})
            report(result[0]["text"])
        report("Reading page and console")
        report(str(await crew.call("browser_evaluate", {
            "expression": "({title:document.title, text:document.body.innerText.slice(0,200)})"})))
        report(str(await crew.call("browser_console", {})))
        (folder / "session.json").write_text(json.dumps({
            "url": runner.browser.sessions[task.id].url, "title": runner.browser.sessions[task.id].title,
            "task_id": task.id}, indent=2))
        report("Smoke check passed")
    except Exception as e:
        report(f"FAILED: {type(e).__name__}: {e}")
        outputs = dump_diagnostics(runner.browser, folder)
        for name, text in outputs.items():
            report(f"Saved {folder / name}")
            if name in {"chrome.stderr.log", "proxy.json"}:
                report(f"{name} (last 8000 characters):\n{text[-8000:] or '(empty)'}")
        raise
    finally:
        report("Closing browser and throwaway store; saved diagnostics stay in browser-evidence")
        await runner.shutdown()
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:5173", help="Preview URL; use a literal IP to select IPv4/IPv6")
    asyncio.run(main(parser.parse_args().url))
