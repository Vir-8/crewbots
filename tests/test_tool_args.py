"""What bots send their crew tools, read as the tools take it: the schema each bot is shown (crewbots.tool_args), and
what a call makes of what it sent. The calls here are ones bots made on 30 Oct 2026, each refused at the time."""

import json

import jsonschema
import pytest
from fakes import ScriptedCrew, bot

from crewbots import tool_args
from crewbots.crew_tools import SPECS, CrewTools, ToolRefused
from crewbots.store import Store

SENT = [  # Claude Code checks a call against the schema before crewbots sees it: these never reached it
    ("handoff", {"done": "Fixed the rename", "output": "src/crewbots/runner/in_place.py\ntests/test_in_place.py",
                 "evidence": "uv run pytest -q: 1206 passed",
                 "checks": "uv run pytest -q tests/test_in_place.py\nuv run ruff check src tests"}),
    ("handoff", {"done": "Trajectory is now the hero", "checks": ""}),
    ("share", {"files": "/tmp/claude-501/shots/Game_1.png"}),
    ("working_state", {"action": "remove", "id": "18e334e697f38618d3d739aa"}),
    ("working_state", {}),
    ("edit_page", {"page": "Scratchpad", "find": "- Rename fix in review", "replace": ""}),
]


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def read(name, sent):
    return tool_args.read(name, SPECS[name].args, sent)


@pytest.mark.parametrize(("name", "sent"), SENT)
def test_what_bots_send_passes_the_schema_they_are_shown(name, sent):
    jsonschema.validate(sent, SPECS[name].schema)


def test_every_tool_is_shown_one_flat_object():
    for name, spec in SPECS.items():
        jsonschema.Draft202012Validator.check_schema(spec.schema)
        text = json.dumps(spec.schema)
        assert spec.schema["type"] == "object" and "$ref" not in text and "$defs" not in text, name
        assert spec.schema["required"] == [k for k, f in spec.args.model_fields.items() if f.is_required()], name
    assert "title" in SPECS["start_thread"].schema["properties"]  # a field's name isn't a schema's title


def test_a_list_sent_as_a_string_is_read_a_line_each():
    args, unknown = read(*SENT[0])
    assert args["output"] == ["src/crewbots/runner/in_place.py", "tests/test_in_place.py"]
    assert args["checks"] == ["uv run pytest -q tests/test_in_place.py", "uv run ruff check src tests"]
    assert args["evidence"] == ["uv run pytest -q: 1206 passed"] and unknown == []
    assert read(*SENT[1])[0]["checks"] == []


def test_the_names_bots_reach_for_are_read_as_the_field():
    args, unknown = read("working_state", {"action": "remove", "id": "18e3"})
    assert (args["action"], args["entry_id"], unknown) == ("remove", "18e3", [])
    assert read("share", {"path": "a.png"})[0]["files"] == ["a.png"]
    assert read("browser_evaluate", {"script": "1+1"})[0] == {"expression": "1+1"}
    assert read("edit_page", {"page": "P", "old": "a", "new": "b"})[0] == {"page": "P", "find": "a", "replace": "b"}
    assert read("write_page", {"page": "P", "text": "Hi"})[0] == {"page": "P", "content": "Hi"}
    assert read("working_state", {"action": ""})[0]["action"] == "list"  # what Codex's MCP server sends for none


def test_a_tool_reads_its_defaults_but_a_forwarded_one_only_what_was_sent():
    assert read("sweep_worktrees", {})[0] == {"dry_run": True}
    assert read("handoff", {"done": "x"})[0] == {"done": "x", "output": [], "open": "", "needs": "", "evidence": [],
                                                 "checks": []}
    assert read("browser_screenshot", {"width": 800})[0] == {"width": 800}  # the browser's own defaults for the rest


def test_what_a_tool_cant_take_is_said_for_the_bot():
    with pytest.raises(tool_args.ArgsError, match="^handoff needs done$"):
        read("handoff", {"output": "a.py"})
    with pytest.raises(tool_args.ArgsError, match="^handoff needs done$"):
        read("handoff", {"done": ""})
    with pytest.raises(tool_args.ArgsError, match="^working_state: action: Input should be 'list', 'add'"):
        read("working_state", {"action": "tidy"})
    assert read(*SENT[5])[0]["replace"] == ""  # replaced with nothing: taken out
    assert read("review", {"verdict": "PASS", "summary": "ok"})[0]["verdict"] == "pass"
    assert [c["label"] for c in read("escalate", {"question": "Which?", "choices": ["A", {"label": "B"}]})[0]["choices"]
            ] == ["A", "B"]
    assert read("handoff", {"done": "x", "task": "Fix it", "to": "eng-lead"})[1] == ["task", "to"]


async def test_a_name_a_tool_doesnt_take_is_left_out_and_the_bot_told(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("engineer"))
    task = store.create_task("engineer", "x")
    tools = CrewTools(crew.runner, task.id, can_delegate=False)

    reply = await tools.call("handoff", {"task": "Fix it", "to": "eng-lead", "done": "Fixed", "checks": "uv run pytest"})
    assert reply.startswith("Handoff recorded.") and reply.endswith(
        "(handoff takes no `task` or `to`, so they were left out. It takes done, output, open, needs, evidence, checks.)")
    assert store.latest_handoff(task.id).checks == ["uv run pytest"]
    with pytest.raises(ToolRefused, match="^handoff needs done$"):
        await tools.call("handoff", {"summary": "Fixed"})


async def test_a_working_state_entry_is_changed_by_its_id_alone(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief"))
    tools = CrewTools(crew.runner, crew.runner.chat("chief").id, can_delegate=False)

    added = await tools.call("working_state", {"section": "Decisions", "action": "add", "text": "Ship on Friday"})
    entry = added.split()[3]  # Working state entry <id> added.
    await tools.call("working_state", {"action": "replace", "id": entry, "text": "Ship on Monday"})
    assert f"`{entry}` Ship on Monday" in await tools.call("working_state", {"section": "Decisions"})
    assert await tools.call("working_state", {"action": "remove", "id": entry}) == f"Working state entry {entry} removed."
    assert "Ship on" not in await tools.call("working_state", {})
    with pytest.raises(ToolRefused, match=f"no entry '{entry}' in your working state"):
        await tools.call("working_state", {"action": "remove", "id": entry})
    with pytest.raises(ToolRefused, match="say which section to change"):
        await tools.call("working_state", {"action": "add", "text": "Ship"})
