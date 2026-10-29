"""Push and Replace cards are only made for a commit the repo's CI steps pass on, run on that commit."""

import asyncio
import subprocess
import textwrap
from pathlib import Path

import pytest
from fakes import bot, git_repo, make_crew
from test_replace_main import BRANCH, IDENT, clean_history
from test_replace_main import setup as replace_setup  # noqa: F401  (a fixture)

from crewbots import ci, paths
from crewbots.projects import Project
from crewbots.push import propose as push_propose
from crewbots.replace_main import propose as replace_propose
from crewbots.runner import NotAllowed, Runner
from crewbots.store import Store

WORKFLOW = textwrap.dedent("""\
    name: checks
    on:
      push:
        branches: [main]
    env:
      GREETING: hello
    jobs:
      first:
        steps:
          - uses: actions/checkout@v7
          - uses: actions/setup-python@v6
            with:
              python-version: "3.11"
          - name: Check
            run: test "$GREETING" = hello && test -f marker.txt && echo "$UV_PYTHON" > python.txt
      second:
        defaults:
          run:
            working-directory: sub
        steps:
          - run: test -f inner.txt
          - if: always()
            run: "true"
    """)


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, message: str, **files: str) -> str:
    for name, text in files.items():
        path = repo / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(repo, "add", "-A")
    git(repo, *IDENT, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def with_workflow(repo: Path, text: str = WORKFLOW, **files: str) -> str:
    return commit(repo, "Add CI", **{".github__workflows__checks.yml": text, "marker.txt": "x", "sub__inner.txt": "y"}, **files)


@pytest.fixture(autouse=True)
def temp(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "temp", lambda: tmp_path / "daemon-tmp")


@pytest.fixture
def push_setup(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    repo = git_repo(tmp_path / "repo")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    store = Store(":memory:")
    crew = make_crew(tmp_path, bot("chief", can_delegate=True), bot("engineer"))
    crew.projects["demo"] = Project("demo", repo, "", tmp_path / "demo.md")
    runner = Runner(store, crew)
    runner.send = lambda *a, **kw: None
    yield runner, repo
    store.close()


def push(runner):
    chat = runner.chat("chief")
    asyncio.run(push_propose(runner, chat.id, "demo", "Ship it"))
    return runner.store.last_event(chat.id, ("push_proposed",))


def test_a_passing_run_makes_the_card_with_the_sha_and_steps(push_setup):
    runner, repo = push_setup
    sha = with_workflow(repo)
    card = push(runner).data
    assert card["ci"]["status"] == "passed" and card["ci"]["sha"] == sha
    assert card["ci"]["note"] == f"CI passed locally on {sha[:12]}"
    assert card["ci"]["steps"] == ["first: Check", "second: test -f inner.txt", "second: true"]
    assert any("setup-python" in s for s in card["ci"]["skipped"])
    assert "error" not in card


def test_a_failing_step_refuses_the_card_with_its_name_and_output(push_setup):
    runner, repo = push_setup
    with_workflow(repo, WORKFLOW.replace("test -f inner.txt", "seq 1 60; echo broken >&2; exit 3"))
    chat = runner.chat("chief")
    with pytest.raises(NotAllowed, match=r"step 'second: seq 1 60.*exit 3"):
        asyncio.run(push_propose(runner, chat.id, "demo", "Ship it"))
    assert runner.store.last_event(chat.id, ("push_proposed",)) is None


def test_the_failure_shows_only_the_last_40_lines(push_setup):
    runner, repo = push_setup
    with_workflow(repo, WORKFLOW.replace("test -f inner.txt", "seq 1 100; exit 1"))
    with pytest.raises(NotAllowed) as e:
        asyncio.run(push_propose(runner, runner.chat("chief").id, "demo", "Ship it"))
    text = str(e.value)
    assert "\n100" in text and "\n61" in text and "\n60\n" not in text


def test_a_project_with_no_checks_gets_a_card_saying_nothing_was_run(push_setup):
    runner, repo = push_setup
    commit(repo, "Work", **{"a.txt": "a"})
    runner.crew.projects["demo"] = Project("demo", repo, "", Path("x.md"))
    card = push(runner).data
    assert "error" not in card and card["ci"]["status"] == "none" and card["ci"]["note"] == "No CI configured, so nothing was run"
    # a workflow that doesn't run on this branch counts as none
    commit(repo, "Other", **{".github__workflows__x.yml": "on: pull_request\njobs:\n  j:\n    steps:\n      - run: 'true'\n"})
    assert push(runner).data["ci"]["status"] == "none"


def test_a_project_can_list_the_commands_when_there_is_no_workflow(push_setup, tmp_path):
    runner, repo = push_setup
    commit(repo, "Work", **{"a.txt": "a"})
    runner.crew.projects["demo"] = Project("demo", repo, "", tmp_path / "demo.md", ci=("test -f a.txt",))
    assert push(runner).data["ci"]["steps"] == ["test -f a.txt"]
    runner.crew.projects["demo"] = Project("demo", repo, "", tmp_path / "demo.md", ci=("test -f nope.txt",))
    with pytest.raises(NotAllowed, match="test -f nope.txt"):
        asyncio.run(push_propose(runner, runner.chat("chief").id, "demo", "Ship it"))


def test_it_runs_on_the_committed_files_not_the_working_folder(push_setup):
    runner, repo = push_setup
    with_workflow(repo, WORKFLOW.replace("test -f inner.txt", "test ! -f untracked.txt"))
    (repo / "marker.txt").unlink()  # committed, but missing from the folder
    (repo / "untracked.txt").write_text("only here")  # in the folder, not in the commit
    assert push(runner).data["ci"]["status"] == "passed"


def test_the_checkout_is_removed_and_the_folder_untouched(push_setup, tmp_path):
    runner, repo = push_setup
    with_workflow(repo)
    before = git(repo, "status", "--porcelain")
    push(runner)
    assert list((tmp_path / "daemon-tmp").iterdir()) == []
    assert git(repo, "status", "--porcelain") == before and git(repo, "worktree", "list").count("\n") == 0


def test_replace_main_checks_the_replacement_tip(replace_setup):  # noqa: F811
    runner, client, repo = replace_setup
    workflow = "on: push\njobs:\n  t:\n    steps:\n      - run: test -f %s\n"
    commit(repo, "Add CI", **{".github__workflows__checks.yml": workflow % "one.txt"})
    clean_history(repo)  # the replacement has main's tree, so the same workflow
    chat = runner.chat("chief")
    asyncio.run(replace_propose(runner, chat.id, "demo", BRANCH, "Replace"))
    card = runner.store.last_event(chat.id, ("replace_main_proposed",)).data
    assert card["ci"]["sha"] == git(repo, "rev-parse", BRANCH) and card["ci"]["status"] == "passed"
    commit(repo, "Break CI", **{".github__workflows__checks.yml": workflow % "nope.txt"})
    clean_history(repo)
    with pytest.raises(NotAllowed, match="CI failed on"):
        asyncio.run(replace_propose(runner, chat.id, "demo", BRANCH, "Replace"))
    assert len(runner.store.db.execute("select id from events where kind = 'replace_main_proposed'").fetchall()) == 1


def test_workflow_triggers_and_unsupported_things(tmp_path):
    def plan(text, branch="main"):
        flow = tmp_path / ".github" / "workflows"
        flow.mkdir(parents=True, exist_ok=True)
        (flow / "a.yml").write_text(text)
        return ci.plan_for(tmp_path, branch)

    assert plan("on: [push, pull_request]\njobs:\n  j:\n    steps:\n      - run: echo hi\n").steps[0].run == "echo hi"
    assert plan("on: pull_request\njobs:\n  j:\n    steps:\n      - run: echo hi\n").steps == []
    assert plan("on:\n  push:\n    branches: [release/*]\njobs:\n  j:\n    steps:\n      - run: x\n").steps == []
    assert plan("on:\n  push:\n    branches-ignore: [main]\njobs:\n  j:\n    steps:\n      - run: x\n").steps == []
    node = "on: push\njobs:\n  j:\n    steps:\n      - uses: actions/setup-node@v7\n        with:\n          node-version: 22\n"
    assert plan(node).steps == [] and plan(node).node == "22"
    for text in ("on: push\njobs:\n  j:\n    strategy:\n      matrix:\n        v: [1]\n    steps:\n      - run: x\n",
                 "on: push\njobs:\n  j:\n    steps:\n      - run: echo ${{ github.sha }}\n",
                 "on: push\njobs:\n  j:\n    steps:\n      - run: x\n        shell: pwsh\n"):
        with pytest.raises(NotAllowed):
            plan(text)


def test_a_projects_ci_list_is_read_and_kept_when_the_project_is_saved(tmp_path):
    from crewbots import projects

    folder = tmp_path / "web"
    folder.mkdir()
    (tmp_path / "crew" / "projects").mkdir(parents=True)
    path = tmp_path / "crew" / "projects" / "web.md"
    path.write_text(f"---\nfolder: {folder}\nci:\n  - make test\n---\nSite\n")
    assert projects.parse(path.read_text(), path).ci == ("make test",)
    assert projects.save(tmp_path / "crew", "web", folder, "Site, edited").ci == ("make test",)


def test_the_workflows_node_major_is_used(tmp_path, monkeypatch):
    fake = tmp_path / "node22" / "bin"
    fake.mkdir(parents=True)
    (fake / "node").write_text("#!/bin/sh\necho v22.3.0\n")
    (fake / "node").chmod(0o755)
    monkeypatch.setattr(ci, "_node_at", lambda d: "v25.1.0" if d is None else ci.subprocess.run(
        [str(Path(d) / "node"), "--version"], capture_output=True, text=True).stdout.strip())
    monkeypatch.setattr(ci.glob, "glob", lambda pattern: [str(fake)] if "node@22" in pattern else [])
    assert ci.find_node("22") == (str(fake), "v22.3.0")
    assert ci.find_node("25") == (None, "v25.1.0")
    with pytest.raises(NotAllowed, match="Node 18 is required by CI but isn't installed"):
        ci.find_node("18")


def test_node_steps_run_under_that_node_and_a_missing_one_refuses(push_setup, monkeypatch, tmp_path):
    runner, repo = push_setup
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "node").write_text("#!/bin/sh\necho v22.3.0\n")
    (fake / "node").chmod(0o755)
    flow = ("on: push\njobs:\n  j:\n    steps:\n      - uses: actions/setup-node@v7\n        with:\n          node-version: 22\n"
            f"      - run: test \"$(node --version)\" = v22.3.0 && test \"$HOME\" != \"{Path.home()}\"\n")
    commit(repo, "CI", **{".github__workflows__c.yml": flow})
    monkeypatch.setattr(ci, "find_node", lambda major: (str(fake), "v22.3.0"))
    card = push(runner).data
    assert card["ci"]["status"] == "passed" and "Node v22.3.0" in card["ci"]["note"]
    monkeypatch.undo()
    monkeypatch.setattr(paths, "temp", lambda: tmp_path / "daemon-tmp")
    monkeypatch.setattr(ci, "_node_at", lambda d: "")
    commit(repo, "More", **{"b.txt": "b"})  # a different push: the same one again would be the open card
    with pytest.raises(NotAllowed, match="Node 22 is required by CI but isn't installed"):
        asyncio.run(push_propose(runner, runner.chat("chief").id, "demo", "Ship it"))


def test_only_setup_steps_may_be_skipped(tmp_path):
    def plan(steps):
        flow = tmp_path / ".github" / "workflows"
        flow.mkdir(parents=True, exist_ok=True)
        (flow / "a.yml").write_text("on: push\njobs:\n  j:\n    steps:\n" + steps)
        return ci.plan_for(tmp_path, "main")

    ok = plan("      - uses: actions/checkout@v7\n      - uses: astral-sh/setup-uv@v10\n      - uses: actions/cache@v4\n"
              "      - if: always()\n        run: echo hi\n      - run: echo there\n")
    assert [s.run for s in ok.steps] == ["echo hi", "echo there"]
    for steps, where in (("      - name: Lint\n        uses: some/linter@v1\n", "step j: Lint"),
                         ("      - if: github.event_name == 'push'\n        run: echo x\n", "step j: echo x"),
                         ("      - if: failure()\n        run: echo x\n", "step j: echo x")):
        with pytest.raises(NotAllowed, match=f"can't reproduce CI {where} locally"):
            plan(steps)
    for uses in ("actions/cache-security-scan@v1", "actions/checkout-evil@v1", "actions/cache"):
        if uses == "actions/cache":
            assert plan(f"      - uses: {uses}\n").steps == []
        else:
            with pytest.raises(NotAllowed, match="can't reproduce CI step"):
                plan(f"      - uses: {uses}\n")
    job = "on: push\njobs:\n  j:\n    if: github.ref == 'x'\n    steps:\n      - run: x\n"
    (tmp_path / ".github" / "workflows" / "a.yml").write_text(job)
    with pytest.raises(NotAllowed, match="can't reproduce CI job j locally"):
        ci.plan_for(tmp_path, "main")


def test_a_checkout_that_cannot_be_removed_is_reported(push_setup, monkeypatch):
    runner, repo = push_setup
    commit(repo, "CI", **{".github__workflows__c.yml": "on: push\njobs:\n  j:\n    steps:\n      - run: 'true'\n"})
    monkeypatch.setattr(ci.shutil, "rmtree", lambda *a, **kw: None)
    card = push(runner).data["ci"]
    assert "temporary checkout left at" in card["note"] and card["cleanup"] in card["note"]
    monkeypatch.setattr(ci.shutil, "rmtree", lambda *a, **kw: None)
    commit(repo, "Break", **{".github__workflows__c.yml": "on: push\njobs:\n  j:\n    steps:\n      - run: 'false'\n"})
    with pytest.raises(NotAllowed, match="(?s)CI failed.*temporary checkout left at"):
        asyncio.run(push_propose(runner, runner.chat("chief").id, "demo", "Ship it"))


def test_a_job_with_no_steps_or_a_reusable_workflow_refuses(tmp_path):
    flow = tmp_path / ".github" / "workflows"
    flow.mkdir(parents=True)
    for job in ("    uses: ./.github/workflows/other.yml\n", "    runs-on: ubuntu-latest\n", "    steps: []\n"):
        (flow / "a.yml").write_text("on: push\njobs:\n  j:\n" + job)
        with pytest.raises(NotAllowed, match=r"can't reproduce CI job j locally \(reusable workflow or no steps\)"):
            ci.plan_for(tmp_path, "main")
