"""Updating crewbots on this Mac from the app: what's newer in its checkout (or on GitHub, when it can come in as a
fast-forward) than what's running, and the release that brings it in.

The release is `scripts/release`: checks, build, install, the daemon restarted once the crew is idle, the app
relaunched. It runs apart from the daemon (its own session), since it restarts the daemon and quits the app it was
started from, and says how far it got in a status file the daemon reads back for the app.

An update does only what it needs, part by part. The daemon runs from the checkout itself, so a change to it needs only
a restart; the app is built again only when its own code changed, and its build is incremental. A change to neither
(tests, docs) isn't an update at all. The checks are the quick ones, that the daemon loads and the app compiles: a
change was checked when it was made.
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
        head = self._git("rev-parse", "--short=12", "HEAD")
        out["head"] = head
        if self._git("status", "--porcelain", "--untracked-files=no"):
            return {**out, "note": "The checkout has changes in progress: update once they're committed."}
        behind = int(self._git("rev-list", "--count", "HEAD..@{u}") or 0)
        ahead = int(self._git("rev-list", "--count", "@{u}..HEAD") or 0)
        out["pull"] = behind > 0 and ahead == 0  # it comes in as a fast-forward
        target = "@{u}" if out["pull"] else "HEAD"
        out["needs"] = {"app": self._behind(app, target, APP), "daemon": self._behind(self.running, target, DAEMON)}
        out["available"] = any(out["needs"].values())
        new = {c["sha"]: c for base, where in ((app, APP), (self.running, DAEMON)) if self._known(base)
               for c in self._commits(target, base, where)}  # what each part doesn't have, of what it runs
        out["commits"] = sorted(new.values(), key=lambda c: c["at"], reverse=True)[:MAX_COMMITS]
        if self._fetch_error and not out["available"]:
            out["note"] = self._fetch_error
        return out

    def _behind(self, built: str | None, target: str, where: tuple[str, ...]) -> bool:
        """Whether what a part runs (`where`) changed after it was built from `built`. One built from a commit this
        checkout doesn't have is behind; one that doesn't say what it was built from isn't."""
        if not built:
            return False
        if not self._known(built):
            return True
        return self._git("diff", "--quiet", built, target, "--", *where) is None

    def _known(self, commit: str | None) -> bool:
        return bool(commit) and self._git("cat-file", "-e", f"{commit}^{{commit}}") is not None

    def _commits(self, target: str, base: str, where: tuple[str, ...]) -> list[dict[str, str]]:
        listed = self._git("log", f"--max-count={MAX_COMMITS}", "--format=%h%x09%cI%x09%s", target, "--not", base, "--",
                           *where)
        return [dict(zip(("sha", "at", "subject"), line.split("\t", 2), strict=True))
                for line in (listed or "").splitlines() if line.count("\t") >= 2]

    def start(self, app: str | None = None) -> dict[str, Any]:
        """Starts the update, after bringing in what GitHub has if that's a fast-forward: the app built again if its
        code changed, and the quick checks. One already under way is the app's to follow."""
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

    def _git(self, *args: str, timeout: float = 15) -> str | None:
        try:
            done = subprocess.run(["git", "-C", str(self.project), *args], capture_output=True, text=True,
                                  timeout=timeout)
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
