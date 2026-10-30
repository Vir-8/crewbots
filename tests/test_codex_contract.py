"""The Codex app-server protocol crewbots speaks by hand (backends/codex_app_server.py), against the one the installed
Codex describes (`codex app-server generate-json-schema`), and the sandbox profile it sets, against the installed
Codex's sandbox. Codex marks the protocol experimental, so an update can rename or drop what crewbots relies on; this
says what moved before a release goes out. It needs Codex, so it runs on the Mac (scripts/release runs the tests) and
is skipped where Codex isn't installed, as in CI."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from crewbots import paths
from crewbots.backends import Access, RunSpec
from crewbots.backends.base import temp_env
from crewbots.backends.codex_cli import sandbox_overrides

pytestmark = pytest.mark.skipif(shutil.which("codex") is None, reason="needs Codex installed")

# What crewbots sends: each request, and the fields it fills in.
REQUESTS = {
    "initialize": ("InitializeParams", {"clientInfo", "capabilities"}),
    "thread/start": ("ThreadStartParams", {"cwd", "approvalPolicy", "sandbox", "developerInstructions", "model"}),
    "thread/resume": ("ThreadResumeParams", {"threadId", "cwd", "approvalPolicy", "sandbox", "developerInstructions",
                                             "model", "excludeTurns"}),
    "turn/start": ("TurnStartParams", {"threadId", "input", "effort"}),
    "turn/steer": ("TurnSteerParams", {"threadId", "expectedTurnId", "input"}),
    "thread/compact/start": ("ThreadCompactStartParams", {"threadId"}),
}
# What it reads: the notifications and the approval requests it answers, and the fields it takes from them.
NOTIFICATIONS = {"item/started", "item/completed", "thread/tokenUsage/updated", "account/rateLimits/updated", "error",
                 "turn/completed"}
APPROVALS = {
    "item/commandExecution/requestApproval": ("CommandExecutionRequestApprovalParams", {"command", "networkApprovalContext"},
                                              "CommandExecutionApprovalDecision"),
    "item/fileChange/requestApproval": ("FileChangeRequestApprovalParams", {"itemId"}, "FileChangeApprovalDecision"),
}
FIELDS = {
    "NetworkApprovalContext": {"host"},
    "Turn": {"id", "status", "error"},
    "TurnError": {"message", "codexErrorInfo"},
    "TokenUsageBreakdown": {"inputTokens", "outputTokens", "reasoningOutputTokens", "cachedInputTokens"},
    "RateLimitSnapshot": {"primary", "secondary"},
    "RateLimitWindow": {"usedPercent", "windowDurationMins", "resetsAt"},
}
ITEMS = {  # the thread items it turns into events, and what it reads of each
    "agentMessage": {"text"},
    "contextCompaction": set(),
    "commandExecution": {"id", "command", "aggregatedOutput", "exitCode", "status"},
    "fileChange": {"id", "changes", "status"},
    "mcpToolCall": {"id", "server", "tool", "arguments", "result", "error", "status"},
    "webSearch": {"id", "query"},
    "imageView": {"id", "path"},
    "imageGeneration": {"id", "revisedPrompt", "savedPath", "status"},
}


@pytest.fixture(scope="module")
def schema(tmp_path_factory):
    out = tmp_path_factory.mktemp("codex-schema")
    subprocess.run(["codex", "app-server", "generate-json-schema", "--out", str(out)], check=True, capture_output=True,
                   timeout=60)
    return json.loads((out / "codex_app_server_protocol.schemas.json").read_text())["definitions"]


def methods(schema, union):
    return {v["properties"]["method"]["enum"][0] for v in schema[union]["oneOf"]}


def definition(schema, name):
    return schema["v2"].get(name) or schema[name]


def fields(schema, name):
    return set(definition(schema, name).get("properties", {}))


def variants(schema, name):
    """A tagged union's variants by their `type`, each with its fields."""
    return {v["properties"]["type"]["enum"][0]: set(v["properties"]) for v in definition(schema, name)["oneOf"]}


def test_what_crewbots_sends_is_still_in_the_protocol(schema):
    assert set(REQUESTS) <= methods(schema, "ClientRequest")
    assert "initialized" in methods(schema, "ClientNotification")
    for method, (params, sent) in REQUESTS.items():
        assert sent <= fields(schema, params), f"{method} no longer takes {sent - fields(schema, params)}"
    assert {"type", "text", "text_elements"} <= variants(schema, "UserInput")["text"]


def test_what_crewbots_reads_is_still_in_the_protocol(schema):
    assert NOTIFICATIONS <= methods(schema, "ServerNotification")
    assert set(APPROVALS) <= methods(schema, "ServerRequest")
    for method, (params, read, decision) in APPROVALS.items():
        assert read <= fields(schema, params), f"{method} no longer has {read - fields(schema, params)}"
        decisions = {v.get("enum", [None])[0] for v in definition(schema, decision)["oneOf"]}
        assert {"accept", "decline"} <= decisions, method
    for name, read in FIELDS.items():
        assert read <= fields(schema, name), f"{name} no longer has {read - fields(schema, name)}"
    items = variants(schema, "ThreadItem")
    for kind, read in ITEMS.items():
        assert kind in items and read <= items[kind], f"{kind}: {read - items.get(kind, set())}"


def sandboxed() -> bool:
    """Whether these tests run in a sandbox already, a bot's: Codex's can't start inside it."""
    return subprocess.run(["sandbox-exec", "-p", "(version 1)(allow default)", "true"], capture_output=True).returncode != 0


def in_sandbox(overrides: list[str], work: Path, steps: dict[str, str], env=None) -> dict[str, tuple[int, str]]:
    """Runs the steps in one Codex sandbox, which holds every process under it, and gives each step's exit status and
    output."""
    script = "".join(f'out=$({{ {command}; }} 2>/dev/null); echo "{name} $? $out"\n' for name, command in steps.items())
    ran = subprocess.run(["codex", "sandbox", *overrides, "--", "sh", "-c", script], cwd=work, capture_output=True,
                         text=True, timeout=60, env=env)
    results = {}
    for line in ran.stdout.splitlines():
        name, status, out = line.split(" ", 2)
        results[name] = int(status), out
    assert set(results) == set(steps), ran.stderr
    return results


def q(path: str | Path) -> str:
    return shlex.quote(str(path))


@pytest.mark.skipif(sys.platform != "darwin" or sandboxed(), reason="Codex's macOS sandbox, which can't start in one")
def test_a_reading_bot_writes_in_its_own_temp_folder_and_nowhere_else(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    (tmp_path / "codex-home").mkdir()
    work, temp = tmp_path / "work", tmp_path / "temp"
    work.mkdir()
    temp.mkdir()
    overrides = sandbox_overrides(RunSpec(prompt="", cwd=work, access=Access.READ_ONLY), temp)

    ran = in_sandbox(overrides, work, {
        "mktemp": "mktemp -d",  # a backup of saves, or a screenshot to share, goes here
        "heredoc": "/bin/zsh -c " + q("cat <<EOF\nwritten\nEOF"),
        "touch": f"touch {q(work / 'made')}",
    }, env={**os.environ, **temp_env(temp)})

    assert ran["mktemp"][0] == 0
    assert ran["heredoc"] == (0, "written")
    assert ran["touch"][0] != 0 and not (work / "made").exists()


@pytest.mark.skipif(sys.platform != "darwin" or sandboxed(), reason="Codex's macOS sandbox, which can't start in one")
def test_the_sandbox_profile_keeps_commands_out_of_the_private_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    (tmp_path / "codex-home").mkdir()
    token, rules = paths.crew() / "daemon.json", paths.crew() / "team" / "rules.md"
    rules.parent.mkdir(parents=True)
    token.write_text('{"token": "x"}')
    rules.write_text("Rules.")
    work = tmp_path / "work"
    (work / ".agents" / "skills").mkdir(parents=True)
    overrides = sandbox_overrides(RunSpec(prompt="", cwd=work, access=Access.WORKSPACE_WRITE), tmp_path / "temp")

    ran = in_sandbox(overrides, work, {
        "rules": f"cat {q(rules)}",
        "token": f"cat {q(token)}",
        "work": f"touch {q(work / 'made')}",
        "verify": "mkdir .agents/verify && echo map > .agents/verify/README.md",
        "skills": f"touch {q(work / '.agents' / 'skills' / 'planted.md')}",  # the rest of .agents/ stays shut
        "home": f"touch {q(Path(paths.home()) / 'outside')}",
        "caches": f"touch {q(paths.caches() / 'uv' / 'downloaded')}",  # what uv and npm download, kept
        "crew": f"touch {q(paths.crew() / 'made')}",
    })

    assert ran["rules"] == (0, "Rules.")
    assert ran["token"][0] != 0
    assert ran["work"][0] == 0 and (work / "made").exists()
    assert ran["verify"][0] == 0
    assert ran["skills"][0] != 0
    assert ran["home"][0] != 0
    assert ran["caches"][0] == 0
    assert ran["crew"][0] != 0
