"""A writing bot's shell never signs in as the user: no gh login, no git credential helper, no SSH key or agent."""

import os
import subprocess

from crewbots import paths
from crewbots.backends import Access, ClaudeCLI, RunSpec
from crewbots.backends.claude_sdk import ClaudeAgent, ClaudeAgentSession
from crewbots.backends.credentials import private_paths, without_credentials


def run(argv, env, cwd):
    return subprocess.run(argv, env=os.environ | env, cwd=cwd, capture_output=True, text=True, timeout=30)


def test_git_and_gh_find_no_credentials_but_keep_your_name(tmp_path):
    env = without_credentials(tmp_path / "creds")

    helpers = run([paths.git(), "config", "--get-all", "credential.helper"], env, tmp_path)
    assert helpers.stdout.strip() == ""  # Apple's built-in osxkeychain helper is cleared too
    assert run([paths.git(), "config", "user.name"], env, tmp_path).stdout.strip() == run(
        [paths.git(), "config", "--global", "user.name"], {}, tmp_path).stdout.strip()
    assert "IdentityFile=/dev/null" in env["GIT_SSH_COMMAND"] and env["SSH_AUTH_SOCK"] == ""
    assert list((tmp_path / "creds" / "gh").iterdir()) == []  # gh sees an empty config: not logged in
    assert env["GH_TOKEN"] == "" and env["GITHUB_TOKEN"] == ""


def test_every_writing_bot_gets_it_and_read_only_ones_need_not(tmp_path):
    def claude_env(access):
        spec = RunSpec(prompt="", cwd=tmp_path, access=access)
        return ClaudeAgent().options(spec, ClaudeAgentSession(spec)).env

    assert claude_env(Access.WORKSPACE_WRITE)["GIT_CONFIG_VALUE_0"] == ""
    assert ClaudeCLI().env(RunSpec(prompt="", cwd=tmp_path, access=Access.WORKSPACE_WRITE))["GIT_CONFIG_VALUE_0"] == ""
    assert "GH_CONFIG_DIR" not in claude_env(Access.READ_ONLY)  # no shell at all


def test_the_private_paths_are_the_keys_and_records_not_the_crews_own_files():
    private = private_paths()
    crew = paths.crew()

    assert {crew / "daemon.json", crew / "mcp.json", crew / "crewbots.db", paths.logs()} <= set(private)
    assert {paths.home() / ".ssh", paths.home() / ".aws", paths.home() / ".config" / "gh"} <= set(private)
    assert not any(p in private for p in (crew, crew / "team", crew / "attachments", crew / "skills"))
