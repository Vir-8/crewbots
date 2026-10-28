"""Updating crewbots on this Mac from the app: what's newer in its checkout (or on GitHub, when it can come in as a
fast-forward) than what's running, and the release that brings it in.

The release is `scripts/release`: checks, build, install, the daemon restarted once the crew is idle, the app
relaunched. It runs apart from the daemon (its own session), since it restarts the daemon and quits the app it was
started from, and says how far it got in a status file the daemon reads back for the app.

An update does only what it needs, part by part. The daemon runs from the checkout itself, so a change to it needs only
a restart (and its dependencies brought up to date); the app is built again only when its own code changed, and its
build is incremental. A change to neither (tests, docs) isn't an update at all. No tests run: what's in the checkout
passed them before it got there.
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from crewbots import paths

FETCH_EVERY_S = 600.0  # GitHub is asked again only this often, however often the app checks
MAX_COMMITS = 30
APP = ("app/", ":(exclude)app/**/*.test.ts", ":(exclude)app/**/*.test.tsx")  # what the app is built from
DAEMON = ("src/", "pyproject.toml", "uv.lock")  # what the daemon runs, from the checkout: a restart takes it in


class UpdateError(RuntimeError):
    pass


def status_file() -> Path:
    return paths.root() / "release.json"


def log_file() -> Path:
    return paths.logs() / "release.log"


def build_dir() -> Path:
    """Where an update builds the app (cargo's target folder)."""
    return paths.root() / "build" / "app"


class Updates:
    def __init__(self, project: Path, running: str | None):
        self.project = project  # the checkout the daemon runs from
        self.running = running  # the commit the daemon started from
        self._fetched = 0.0
        self._fetch_error: str | None = None

    def check(self, app: str | None = None, fetch: bool = False) -> dict[str, Any]:
        """What an update would bring this Mac, and which parts it needs: the app built again, the daemon restarted.
        `app` is the commit the app was built from."""
        out: dict[str, Any] = {"app": app, "daemon": self.running, "available": False, "commits": [], "head": None,
                               "pull": False, "note": None, "needs": {"app": False, "daemon": False},
                               "release": self.release()}
        if not (self.project / ".git").exists() or not (self.project / "scripts" / "release").exists():
            return {**out, "note": "crewbots isn't running from a checkout of its own, so it updates some other way."}
        if fetch and time.time() - self._fetched > FETCH_EVERY_S:
            self._fetched = time.time()
            done = self._git("fetch", "--quiet", timeout=60)
            self._fetch_error = None if done is not None else "Couldn't reach GitHub just now."
        # One look at the branch: its commit, whether anything's uncommitted, how far it is from GitHub's.
        status = (self._git("status", "--porcelain=v2", "--branch", "--untracked-files=no") or "").splitlines()
        branch = dict(line[2:].split(" ", 1) for line in status if line.startswith("# ") and " " in line[2:])
        out["head"] = branch["branch.oid"][:12] if branch.get("branch.oid", "(initial)") != "(initial)" else None
        if any(not line.startswith("#") for line in status):
            return {**out, "note": "The checkout has changes in progress: update once they're committed."}
        ahead, behind = (int(n[1:]) for n in branch.get("branch.ab", "+0 -0").split())
        out["pull"] = behind > 0 and ahead == 0  # it comes in as a fast-forward
        target = "@{u}" if out["pull"] else "HEAD"
        at, app_at, daemon_at, release_at = self._resolve(target, app, self.running, out["release"].get("commit"))
        # A completed build is only ready for the checkout it actually built. HEAD (or its upstream) can move
        # after a build, including when someone merges by hand; an older bundle must never stand in for it.
        if out["release"].get("state") == "built" and (not at or release_at != at):
            out["release"] = {}
        new: dict[str, dict[str, str]] = {}  # what each part doesn't have, of what it runs
        for part, built, built_at, where in (("app", app, app_at, APP), ("daemon", self.running, daemon_at, DAEMON)):
            out["needs"][part] = self._behind(built, built_at, at, target, where)
            if out["needs"][part] and built_at:
                new.update((c["sha"], c) for c in self._commits(target, built_at, where))
        out["available"] = any(out["needs"].values())
        if not out["available"] and out["release"].get("state") == "built":
            out["release"] = {}  # the prepared release has already been taken in
        out["commits"] = sorted(new.values(), key=lambda c: c["at"], reverse=True)[:MAX_COMMITS]
        if self._fetch_error and not out["available"]:
            out["note"] = self._fetch_error
        return out

    def _behind(self, built: str | None, built_at: str | None, at: str | None, target: str,
                where: tuple[str, ...]) -> bool:
        """Whether what a part runs (`where`) changed after it was built from `built` (`built_at` is that commit,
        when this checkout has it). One built from a commit this checkout doesn't have is behind; one that doesn't say
        what it was built from isn't."""
        if not built:
            return False
        if not built_at:
            return True
        return built_at != at and self._git("diff", "--quiet", built_at, target, "--", *where) is None

    def _resolve(self, *names: str | None) -> list[str | None]:
        """The commit each name is, looked up at once: None for one this checkout doesn't have."""
        asked = [name for name in names if name and not any(ch.isspace() for ch in name)]
        found: dict[str, str] = {}
        if asked:
            listed = self._git("cat-file", "--batch-check", input="".join(f"{name}^{{commit}}\n" for name in asked))
            for name, line in zip(asked, (listed or "").splitlines(), strict=False):
                sha, _, kind = line.partition(" ")
                if kind.startswith("commit "):
                    found[name] = sha
        return [found.get(name) if name else None for name in names]

    def _commits(self, target: str, base: str, where: tuple[str, ...]) -> list[dict[str, str]]:
        listed = self._git("log", f"--max-count={MAX_COMMITS}", "--format=%h%x09%cI%x09%s", target, "--not", base, "--",
                           *where)
        return [dict(zip(("sha", "at", "subject"), line.split("\t", 2), strict=True))
                for line in (listed or "").splitlines() if line.count("\t") >= 2]

    def start(self, app: str | None = None) -> dict[str, Any]:
        """Starts the update, after bringing in what GitHub has if that's a fast-forward: the app built again if its
        code changed, the daemon's dependencies brought up to date. One already under way is the app's to follow."""
        found = self.check(app)
        if found["release"].get("state") == "running":
            return found
        if not found["available"]:
            raise UpdateError(found["note"] or "crewbots is up to date")
        if found["pull"] and self._git("merge", "--ff-only", "--quiet", "@{u}", timeout=60) is None:
            raise UpdateError("couldn't bring in the new commits as a fast-forward")
        self._release(build_app=found["needs"]["app"])
        return self.check(app)

    def _release(self, build_app: bool) -> None:
        """Runs scripts/release apart from the daemon, saying how long the last update like it took, for the app to
        show how far along this one is."""
        kind = "app" if build_app else "daemon"
        last = self.release()
        took = last["at"] - last["started_at"] if last.get("state") == "built" and last.get("kind") == kind and last.get(
            "at") and last.get("started_at") else 0
        log_file().parent.mkdir(parents=True, exist_ok=True)
        now = int(time.time())
        _write_status({"state": "running", "step": "Starting", "commit": self._git("rev-parse", "--short=12", "HEAD"),
                       "started_at": now, "at": now, "expected": took, "kind": kind})
        with log_file().open("w") as log:
            # The app restarts itself once it's done: it's the one that may relaunch it, with no Apple Events asked of
            # anyone. The build has a folder of its own: macOS doesn't let this process reuse what a build started
            # elsewhere (a terminal) left in the checkout's, which carries that other app's provenance.
            env = {**os.environ, "CREWBOTS_RELEASE_FROM_APP": "1", "CARGO_TARGET_DIR": str(build_dir()),
                   "CREWBOTS_RELEASE_APP": "1" if build_app else "0", "CREWBOTS_RELEASE_EXPECTED": str(took)}
            subprocess.Popen([str(self.project / "scripts" / "release")], cwd=self.project, stdout=log, stderr=log,
                             stdin=subprocess.DEVNULL, start_new_session=True, env=env)

    def release(self) -> dict[str, Any]:
        """How the last release went, as scripts/release left it, with the end of its log if it failed."""
        try:
            state = json.loads(status_file().read_text())
        except (OSError, ValueError):
            return {}
        pid = state.get("pid")
        if state.get("state") == "running" and pid and not _alive(pid):
            state = {**state, "state": "failed", "step": f"{state.get('step')} (it stopped)"}
        if state.get("state") == "failed":
            try:
                state["log"] = "\n".join(log_file().read_text(errors="replace").splitlines()[-12:])
            except OSError:
                pass
        return state

    def _git(self, *args: str, timeout: float = 15, input: str | None = None) -> str | None:
        try:
            done = subprocess.run([paths.git(), "-C", str(self.project), *args], capture_output=True, text=True,
                                  timeout=timeout, input=input)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.stdout.strip() if done.returncode == 0 else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_status(state: dict[str, Any]) -> None:
    status_file().parent.mkdir(parents=True, exist_ok=True)
    tmp = status_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(status_file())
