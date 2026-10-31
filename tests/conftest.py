import os
import sys
import tempfile
import time
from pathlib import Path

# Tests never see the user's own home folder: from here on, this process's HOME is a throwaway one, before any test
# module (or anything it imports) is loaded. Each test then gets a home of its own, below. Taken first, so the guard
# at the end can look at the real one.
REAL_HOME = Path.home()
SESSION_HOME = Path(tempfile.mkdtemp(prefix="crewbots-tests-home-"))
os.environ["HOME"] = str(SESSION_HOME)
os.environ.pop("CREWBOTS_HOME", None)
os.environ.pop("CODEX_HOME", None)
# Run from a git hook or `git rebase --exec`, git hands down the repository it's in: every test's `git init` in a temp
# folder would then reinitialise that repository instead (making it bare), and its commits would land there too.
for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX"):
    os.environ.pop(name, None)

sys.path.insert(0, str(Path(__file__).parent))  # so tests can import helpers

import pytest  # noqa: E402
from fakes import ADMIN, PORT, HeldBackend, bot, make_crew  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from crewbots.api import create_app  # noqa: E402
from crewbots.runner import Runner  # noqa: E402
from crewbots.runner import core as runner_core  # noqa: E402
from crewbots.store import Store  # noqa: E402

GIT_IDENTITY = "[user]\n\tname = crewbots tests\n\temail = tests@crewbots.invalid\n"


@pytest.fixture(autouse=True)
def own_home(tmp_path, monkeypatch):
    """Every test has its own empty home folder, so whatever crewbots keeps there by default (~/.crewbots, Codex's
    and Claude's folders, LaunchAgents) lands in the test's temp folder. Git gets a name to commit with."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(GIT_IDENTITY)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CREWBOTS_HOME", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return home


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    """A turn that failed for a passing reason is tried again at once in tests, not after minutes."""
    monkeypatch.setattr(runner_core, "RETRY_WAITS_S", (0.0, 0.0, 0.0))


@pytest.fixture
def crew_app(tmp_path):
    """The daemon's API over a chief and an engineer whose runs stay open: (client, backend, store)."""
    store = Store(tmp_path / "crew.db")
    backend = HeldBackend()
    crew = make_crew(tmp_path, bot("chief", can_delegate=True), bot("engineer"))
    runner = Runner(store, crew, {"fake": backend}, api_url=f"http://127.0.0.1:{PORT}/v1")
    with TestClient(create_app(runner, ADMIN, PORT), base_url=f"http://127.0.0.1:{PORT}/v1") as client:
        yield client, backend, store
        backend.release.set()
    store.close()


# The guard: nothing in the real ~/.crewbots or ~/.claude may change while the tests run. The crew's daemon may be
# running beside them and writing its own files, so those are left out, and so are the user's own Claude sessions.
LIVE = {".crewbots/crew/crewbots.db", ".crewbots/crew/crewbots.db-wal", ".crewbots/crew/crewbots.db-shm",
        ".crewbots/crew/daemon.json", ".crewbots/logs", ".crewbots/crew/attachments", ".crewbots/crew/memory",
        ".crewbots/tmp", ".crewbots/caches"}
WATCHED_DEPTH = 3  # deep enough for every folder crewbots makes; not into checkouts, which are big


def _snapshot() -> dict[str, float]:
    seen: dict[str, float] = {}

    def walk(path: Path, depth: int) -> None:
        name = str(path.relative_to(REAL_HOME))
        if name in LIVE or any(name.startswith(live + "/") for live in LIVE):
            return
        try:
            seen[name] = path.stat().st_mtime
        except OSError:
            return
        if depth < WATCHED_DEPTH and path.is_dir() and not path.is_symlink():
            for child in path.iterdir():
                walk(child, depth + 1)

    walk(REAL_HOME / ".crewbots", 1)
    # Claude Code keeps a folder per working folder: a test that started it in a temp folder would add one.
    for project in (REAL_HOME / ".claude" / "projects").glob("*"):
        if "pytest" in project.name or "crewbots-tests" in project.name:
            seen[str(project.relative_to(REAL_HOME))] = project.stat().st_mtime
    return seen


def _crew_was_working(since: float) -> bool:
    """Whether the real crew's daemon did any work while the tests ran: then a change may be its, not a test's. A
    bot's sandbox (a bot running these tests on crewbots itself) can't read the crew's records, but then nothing the
    tests run could have changed the user's folders either."""
    import sqlite3

    db = REAL_HOME / ".crewbots" / "crew" / "crewbots.db"
    try:
        if not db.exists():
            return False
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            started = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(since))
            return conn.execute("select count(*) from tasks where updated_at >= ?", (started,)).fetchone()[0] > 0
    except PermissionError:
        return True
    except sqlite3.Error:
        return False


@pytest.fixture(scope="session", autouse=True)
def real_home_untouched():
    started, before = time.time(), _snapshot()
    yield
    after = _snapshot()
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    if changed and not _crew_was_working(started):
        pytest.fail(f"the tests changed the user's own folders: {changed[:10]}", pytrace=False)
