"""Updating crewbots on this Mac from the app: what's newer in its checkout (or on GitHub, when it can come in as a
fast-forward) than what's running, and the release that brings it in.

The release is `scripts/release`: checks, build, install, the daemon restarted once the crew is idle, the app
relaunched. It runs apart from the daemon (its own session), since it restarts the daemon and quits the app it was
started from, and says how far it got in a status file the daemon reads back for the app.

An update in the checkout is got ready before anyone asks: checked and built in the background, at low priority, as
soon as the app's check finds it, so updating is then only installing it and restarting. Not one from GitHub, which
changes the checkout and waits to be asked, and not again for a commit whose build failed.
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
NICE = "10"  # an update got ready in the background makes way for whatever else the Mac is doing


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

    def check(self, app: str | None = None, fetch: bool = False, prepare: bool = False) -> dict[str, Any]:
        """What an update would bring this Mac. `app` is the commit the app was built from. `prepare` starts getting
        one in the checkout ready, if it isn't already."""
        out: dict[str, Any] = {"app": app, "daemon": self.running, "available": False, "commits": [], "head": None,
                               "pull": False, "note": None, "release": self.release()}
        if not (self.project / ".git").exists() or not (self.project / "scripts" / "release").exists():
            return {**out, "note": "crewbots isn't running from a checkout of its own, so it updates some other way."}
        if fetch and time.time() - self._fetched > FETCH_EVERY_S:
            self._fetched = time.time()
            done = self._git("fetch", "--quiet", timeout=60)
            self._fetch_error = None if done is not None else "Couldn't reach GitHub just now."
        head = self._git("rev-parse", "--short=12", "HEAD")
        out["head"] = head
        if self._git("status", "--porcelain", "--untracked-files=no"):
            return {**out, "note": "The checkout has changes in progress: update once they're committed."}
        behind = int(self._git("rev-list", "--count", "HEAD..@{u}") or 0)
        ahead = int(self._git("rev-list", "--count", "@{u}..HEAD") or 0)
        out["pull"] = behind > 0 and ahead == 0  # it comes in as a fast-forward
        target = "@{u}" if out["pull"] else "HEAD"
        known = [c for c in (app, self.running) if c and self._git("cat-file", "-e", f"{c}^{{commit}}") is not None]
        if known:  # what the one further behind (the app or the daemon) doesn't have
            missing = [self._commits(target, base) for base in known]
            out["commits"] = max(missing, key=len)
            out["available"] = bool(out["commits"])
        else:  # built from a commit this checkout doesn't have: anything else is newer
            out["available"] = bool(head) and head not in (app, self.running)
        if self._fetch_error and not out["available"]:
            out["note"] = self._fetch_error
        if prepare and out["available"] and not out["pull"] and not _tried(out["release"], head):
            self._release(background=True)
            out["release"] = self.release()
        return out

    def _commits(self, target: str, base: str) -> list[dict[str, str]]:
        listed = self._git("log", f"--max-count={MAX_COMMITS}", "--format=%h%x09%cI%x09%s", target, "--not", base)
        return [dict(zip(("sha", "at", "subject"), line.split("\t", 2), strict=True))
                for line in (listed or "").splitlines() if line.count("\t") >= 2]

    def start(self, app: str | None = None) -> dict[str, Any]:
        """Starts the release, after bringing in what GitHub has if that's a fast-forward. One already under way (got
        ready in the background, say), or built and ready, is the app's to follow or install."""
        found = self.check(app)
        release = found["release"]
        if release.get("state") == "running" or (release.get("state") == "built" and release.get("commit") == found["head"]):
            return found
        if not found["available"]:
            raise UpdateError(found["note"] or "crewbots is up to date")
        if found["pull"] and self._git("merge", "--ff-only", "--quiet", "@{u}", timeout=60) is None:
            raise UpdateError("couldn't bring in the new commits as a fast-forward")
        self._release()
        return self.check(app)

    def _release(self, background: bool = False) -> None:
        """Runs scripts/release apart from the daemon, saying how long the last build took, for the app to show how
        far along this one is."""
        last = self.release()
        took = last["at"] - last["started_at"] if last.get("state") == "built" and last.get("at") and last.get(
            "started_at") else 0
        log_file().parent.mkdir(parents=True, exist_ok=True)
        now = int(time.time())
        _write_status({"state": "running", "step": "Starting", "commit": self._git("rev-parse", "--short=12", "HEAD"),
                       "started_at": now, "at": now, "expected": took, "background": background})
        with log_file().open("w") as log:
            # The app restarts itself once it's done: it's the one that may relaunch it, with no Apple Events asked of
            # anyone. The build has a folder of its own: macOS doesn't let this process reuse what a build started
            # elsewhere (a terminal) left in the checkout's, which carries that other app's provenance.
            env = {**os.environ, "CREWBOTS_RELEASE_FROM_APP": "1", "CARGO_TARGET_DIR": str(build_dir()),
                   "CREWBOTS_RELEASE_EXPECTED": str(took), **({"CREWBOTS_RELEASE_BACKGROUND": "1"} if background else {})}
            script = str(self.project / "scripts" / "release")
            subprocess.Popen(["nice", "-n", NICE, script] if background else [script], cwd=self.project, stdout=log,
                             stderr=log, stdin=subprocess.DEVNULL, start_new_session=True, env=env)

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

    def _git(self, *args: str, timeout: float = 15) -> str | None:
        try:
            done = subprocess.run(["git", "-C", str(self.project), *args], capture_output=True, text=True,
                                  timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.stdout.strip() if done.returncode == 0 else None


def _tried(release: dict[str, Any], head: str | None) -> bool:
    """Whether the checkout's newest is being built, has been, or failed to be (not tried again on its own)."""
    return release.get("state") == "running" or (release.get("commit") == head and release.get("state") in ("built", "failed"))


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
