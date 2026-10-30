"""Checks handed back with a piece of work, rerun by crewbots before its review. The owner names the commands that prove
it (`handoff`'s `checks`); before a review's first turn they run in the review's throwaway copy of the work, inside the
sandbox a writing bot's commands get (Codex's, with crewbots' own profile and no logins), so they can do no more than
the owner's own commands could. Once per version: a round's reviewers share the run. If one fails, the round fails with
its output as proof and no reviewer's turn is spent; if they all pass, the reviewers are told, and needn't rerun them."""

import asyncio
import logging
import os
import shutil
import signal
import tempfile
import time
from pathlib import Path
from typing import Any

from crewbots import paths
from crewbots.backends import Access, RunSpec
from crewbots.backends.base import package_caches, temp_env
from crewbots.backends.codex_cli import PROFILE, sandbox_overrides
from crewbots.backends.credentials import without_credentials
from crewbots.events import Notice
from crewbots.store import Status, Task, Verdict

MAX_CHECKS = 6  # commands one handoff can have rerun
CHECK_TIMEOUT_S = 10 * 60  # for each
TAIL = 3000  # characters of a failing check's output, kept as its proof

log = logging.getLogger(__name__)


def sandboxed(command: str, cwd: Path, network: bool, temp: Path) -> list[str] | None:
    """The command line that runs `command` in `cwd` as a writing bot's sandbox would; None without Codex."""
    codex = shutil.which("codex")
    if codex is None:
        return None
    spec = RunSpec(prompt="", cwd=cwd, access=Access.WORKSPACE_WRITE, network=network)
    return [codex, "sandbox", "-P", PROFILE, "-C", str(cwd), *sandbox_overrides(spec, temp), "--", "bash", "-c", command]


class Checks:
    """The part of Runner that reruns a piece of work's checks before its review."""

    async def _checks_first(self, review: Task, prompt: str) -> str | None:
        """Before a review's first turn, in its own copy of the work: the prompt to go on with, told how the work's
        checks went, or None once one failed and the review was settled without a turn."""
        await self._worktree_sweep_done.wait()
        if review.turns or not self._in_review_copy(review):
            return prompt
        work = self.store.get_task(review.review_of)
        if work.turns != review.review_version:
            return prompt  # the work moved on since this copy was taken: its latest checks aren't this version's
        handoff = self.store.latest_handoff(work.id)
        if handoff is None or not handoff.checks:
            return prompt
        runs = self.__dict__.setdefault("_check_runs", {})
        key = (work.id, review.review_version)
        if key not in runs:
            runs[key] = asyncio.ensure_future(self._run_checks(work, handoff.checks, Path(review.workspace)))
        results = await asyncio.shield(runs[key])
        if results is None:
            return prompt  # no sandbox to run them in: the reviewers run what matters, as before
        failed = [r for r in results if r["exit"] != 0]
        if not failed:
            ran = "\n".join(f"- `{r['command']}`: passed in {r['seconds']}s" for r in results)
            return (f"{prompt}\n\ncrewbots reran the checks {work.id} was handed back with, in this copy, before your "
                    f"review, and they all passed:\n{ran}\nNo need to run them again: spend the review on what they "
                    "don't cover.")
        summary = (f"Not reviewed: crewbots reran the checks {work.id} was handed back with, in a clean copy, and "
                   f"{len(failed)} of {len(results)} failed.")
        self.review(review.id, Verdict.FAIL, summary, [{
            "location": "checks",
            "problem": f"`{r['command']}` " + ("timed out" if r["exit"] is None else f"exits {r['exit']}")
                       + " in a clean copy of the work",
            "scenario": "the work as it was handed back, rerun in the owner's sandbox in a copy with nothing else in it",
            "proof": f"$ {r['command']}\n{r['tail']}"} for r in failed])
        self._record(review.id, Notice(message=summary))
        self._set(review.id, status=Status.IDLE, result=summary, turns=review.turns + 1)
        return None

    async def _run_checks(self, work: Task, commands: list[str], cwd: Path) -> list[dict[str, Any]] | None:
        bot = self.crew.bots.get(work.bot)
        network = bool(bot and bot.network is True)
        paths.temp().mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix="checks-", dir=paths.temp()))
        env = dict(os.environ) | without_credentials() | package_caches() | temp_env(temp) | {"CI": "true"}
        results: list[dict[str, Any]] = []
        try:
            for command in commands:
                argv = sandboxed(command, cwd, network, temp)
                if argv is None:
                    return None
                started = time.monotonic()
                proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE,
                                                            stderr=asyncio.subprocess.STDOUT, start_new_session=True)
                try:
                    out, _ = await asyncio.wait_for(proc.communicate(), CHECK_TIMEOUT_S)
                    code: int | None = proc.returncode
                except TimeoutError:
                    os.killpg(proc.pid, signal.SIGKILL)
                    out, _ = await proc.communicate()
                    code = None
                text = out.decode(errors="replace").strip()
                results.append({"command": command, "exit": code, "seconds": round(time.monotonic() - started),
                                "tail": text if len(text) <= TAIL else "…" + text[-TAIL:]})
            self._record(work.id, kind="checks_rerun", data={"version": work.turns, "results": results})
            return results
        except OSError:
            log.exception("Could not rerun %s's checks; its reviewers run what matters instead", work.id)
            return None
        finally:
            shutil.rmtree(temp, ignore_errors=True)
