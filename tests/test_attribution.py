"""No AI attribution trailers in the commits crewbots makes: the backends are told not to add them, and every commit
the daemon creates strips them and refuses to land one."""

import json
import subprocess

import pytest
from fakes import git_repo

from crewbots import attribution, paths, worktree
from crewbots.backends import Access, ClaudeAgent, ClaudeCLI, CodexExec, RunSpec
from crewbots.backends.claude_cli import NO_ATTRIBUTION, settings
from crewbots.backends.claude_sdk import ClaudeAgentSession
from crewbots.memory import Memory

CLAUDE = "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
CODEX = "Co-authored-by: Codex <noreply@openai.com>"
HUMAN = "Co-authored-by: Jane Smith <jane@example.com>"


def body(repo, rev="HEAD"):
    return subprocess.run([paths.git(), "show", "-s", "--format=%B", rev], cwd=repo, check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture
def repo(tmp_path):
    return git_repo(tmp_path / "repo")


def add_hook(repo, text):
    for name in ("commit-msg", "prepare-commit-msg"):
        hook = repo / ".git" / "hooks" / name
        hook.write_text(f"#!/bin/sh\nprintf '\\n{text}\\n' >> \"$1\"\n")
        hook.chmod(0o755)


# The settings and flags each backend gets


@pytest.mark.parametrize("access", list(Access))
def test_claude_cli_sessions_turn_attribution_off(access):
    argv = ClaudeCLI().argv(RunSpec(prompt="x", cwd=".", access=access))
    given = json.loads(argv[argv.index("--settings") + 1])
    assert given["includeCoAuthoredBy"] is False and given["attribution"] == {"commit": "", "pr": ""}


@pytest.mark.parametrize("access", list(Access))
def test_claude_sdk_sessions_turn_attribution_off(access):
    spec = RunSpec(prompt="x", cwd=".", access=access)
    given = json.loads(ClaudeAgent(cli_path="/bin/claude").options(spec, ClaudeAgentSession(spec)).settings)
    assert given["includeCoAuthoredBy"] is False and given["attribution"] == {"commit": "", "pr": ""}


def test_the_settings_keep_the_permissions_next_to_attribution():
    assert settings(Access.FULL) == NO_ATTRIBUTION
    assert set(settings(Access.READ_ONLY)) == {"permissions", *NO_ATTRIBUTION}


@pytest.mark.parametrize("access", list(Access))
def test_codex_sessions_turn_attribution_off(access):
    spec = RunSpec(prompt="x", cwd=".", access=access)
    for args in (CodexExec(isolate=False).argv(spec), CodexExec(isolate=False).config_overrides(spec)):
        assert "features.codex_git_commit=false" in args and 'commit_attribution=""' in args


# The helper


def test_only_clearly_ai_trailers_are_removed_and_human_ones_kept():
    message = (f"Fix it\n\nDetails.\n\n{HUMAN}\n{CLAUDE}\n{CODEX}\nco-authored-by: Codex <codex@openai.com>\n"
               "co-authored-by: Copilot <copilot@github.com>\n")
    assert attribution.without_ai(message) == f"Fix it\n\nDetails.\n\n{HUMAN}\nco-authored-by: Copilot <copilot@github.com>\n"
    assert attribution.without_ai("Fix it\n\n" + CLAUDE) == "Fix it"
    assert attribution.without_ai("Fix it\n\nGenerated with [Claude Code](https://claude.com/claude-code)") == "Fix it"
    for who in ("Claude Sonnet 5.5 <noreply@anthropic.com>", "Codex <codex@openai.com>", "Someone <noreply@anthropic.com>",
                "Someone <NoReply@OpenAI.com>", "Someone <codex@openai.com>",
                "claude[bot] <209825114+claude[bot]@users.noreply.github.com>",
                "copilot-swe-agent[bot] <198982749+Copilot@users.noreply.github.com>"):
        assert attribution.without_ai(f"s\n\nCo-Authored-By: {who}") == "s", who
    for who in ("Claude Martin <claude@personal.com>", "Claude <claude@gmail.com>", "Claude Sonnet 5.5 <someone@example.com>",
                "ChatGPT <x@y.z>", "GPT-5 <x@y.z>", "Gemini <x@y.z>", "Claude Code <x@y.z>",
                "Claude Martin <claude.martin@example.org>", "Gaia Smith <gaia@example.com>", "Rabbot Kim <r@e.com>",
                "Gemini Rivera <g@example.com>", "Ai Tanaka <ai@example.jp>", "Codex Jones <c@example.com>",
                "Jane Smith <jane@openai.com>", "Raj Rao <raj@anthropic.com>",
                "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>",
                "renovate[bot] <29139614+renovate[bot]@users.noreply.github.com>"):
        kept = f"s\n\nCo-Authored-By: {who}"
        assert attribution.without_ai(kept) == kept, who
        attribution.check(kept)


def test_folded_trailers_are_found_and_removed_whole():
    folded = "s\n\nCo-authored-by:\n Claude <noreply@anthropic.com>\n"
    assert attribution.without_ai(folded) == "s\n"
    with pytest.raises(attribution.AttributionError):
        attribution.check(folded)
    human = f"s\n\nCo-authored-by:\n Jane Smith <jane@example.com>\n{CLAUDE}\n"
    assert attribution.without_ai(human) == "s\n\nCo-authored-by:\n Jane Smith <jane@example.com>\n"


def test_a_message_that_still_has_a_trailer_is_refused():
    with pytest.raises(attribution.AttributionError):
        attribution.check(f"s\n\n{CLAUDE}")
    attribution.check(f"s\n\n{HUMAN}")
    assert attribution.final_message(f"s\n\n{CLAUDE}\n") == "s\n"


# The commit paths


def test_the_save_of_uncommitted_work_strips_the_trailer(repo, tmp_path):
    wt = worktree.create(repo, "t1", root=tmp_path / "wts")
    (wt.path / "a").write_text("a\n")
    worktree.commit(wt, f"Saved\n\n{HUMAN}\n{CLAUDE}")
    assert body(wt.path).strip() == f"Saved\n\n{HUMAN}"


def test_hooks_do_not_run_so_they_cannot_add_a_trailer(repo, tmp_path):
    wt = worktree.create(repo, "t2", root=tmp_path / "wts")
    add_hook(repo, CLAUDE)
    (wt.path / "a").write_text("a\n")
    worktree.commit(wt, "Saved")
    assert body(wt.path).strip() == "Saved"
    commit = worktree.merge(wt, "Add a")
    assert body(repo, commit).strip() == "Add a"


def test_a_stored_message_that_differs_from_the_checked_one_is_refused_without_a_reset(repo, tmp_path, monkeypatch):
    wt = worktree.create(repo, "t3", root=tmp_path / "wts")
    (wt.path / "a").write_text("a\n")
    monkeypatch.setattr(attribution, "canonical", lambda message: "Saved   \n\n\nbody\n")  # not stripspaced
    with pytest.raises(worktree.GitError, match="other than the one checked"):
        worktree.commit(wt, "Saved")
    assert body(wt.path).strip() == "Saved\n\nbody"  # nothing was rolled back


def test_a_squash_merge_carries_neither_the_summary_s_nor_the_branch_commits_trailers(repo, tmp_path):
    wt = worktree.create(repo, "t4", root=tmp_path / "wts")
    (wt.path / "a").write_text("a\n")
    subprocess.run([paths.git(), "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], cwd=wt.path, check=True)
    subprocess.run([paths.git(), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm",
                    f"Branch commit\n\n{CLAUDE}"], cwd=wt.path, check=True)
    assert "Claude" in body(wt.path)
    commit = worktree.merge(wt, f"Add a\n\nWhy.\n\n{CLAUDE}\n{HUMAN}")
    assert body(repo, commit).strip() == f"Add a\n\nWhy.\n\n{HUMAN}"
    assert (repo / "a").read_text() == "a\n"


def test_a_real_squash_merge_with_untidy_whitespace_in_the_summary_merges_cleanly(repo, tmp_path):
    wt = worktree.create(repo, "t5", root=tmp_path / "wts")
    (wt.path / "a").write_text("a\n")
    worktree.commit(wt, "Branch commit")
    commit = worktree.merge(wt, "\n\nAdd a   \n\n\n\nWhy it matters.\t\n\n\n")
    assert body(repo, commit).strip() == "Add a\n\nWhy it matters."
    assert (repo / "a").read_text() == "a\n"


def test_the_stored_memory_message_is_verified_without_a_reset(tmp_path, monkeypatch):
    memory = Memory(tmp_path)
    memory.root.mkdir(exist_ok=True)
    (memory.root / "note.md").write_text("x\n")
    memory.commit("first")
    (memory.root / "note.md").write_text("y\n")
    monkeypatch.setattr(attribution, "canonical", lambda message: "untidy   \n\n\n\nmessage\n")  # not stripspaced
    with pytest.raises(attribution.AttributionError, match="other than the one checked"):
        memory.commit("second")
    assert body(memory.root).strip() == "untidy\n\nmessage"  # the commit stays


def test_the_memory_repository_strips_the_trailer_and_runs_no_hooks(tmp_path):
    memory = Memory(tmp_path)
    memory.root.mkdir(exist_ok=True)
    (memory.root / "note.md").write_text("x\n")
    memory.commit("first")
    hook = memory.root / ".git" / "hooks" / "commit-msg"
    hook.write_text(f"#!/bin/sh\nprintf '\\n{CODEX}\\n' >> \"$1\"\n")
    hook.chmod(0o755)
    (memory.root / "note.md").write_text("y\n")
    memory.commit(f"Consolidated\n\n{CLAUDE}")
    assert body(memory.root).strip() == "Consolidated"
