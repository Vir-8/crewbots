"""Routines: cron schedules, routine files, the scheduler, and GitHub triggers."""

import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fakes import ScriptedCrew, bot, reply, wait_until
from fastapi.testclient import TestClient

from crewbots import crew as crew_module
from crewbots import routines
from crewbots.api import create_app
from crewbots.events import Done
from crewbots.github import PullRequest, Snapshot, _prs
from crewbots.routines import Cron, RoutineError
from crewbots.runner import Runner
from crewbots.scheduler import MAX_STARTS_PER_POLL, Scheduler, activity, corrections
from crewbots.store import Store, Verdict

TZ = datetime.now().astimezone().tzinfo
MONDAY_9 = datetime(2026, 10, 26, 9, 0, tzinfo=TZ)  # a Monday


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


@pytest.fixture
def crew(store, tmp_path):
    scripted = ScriptedCrew(store, tmp_path, bot("chief"), bot("reviewer"))
    (tmp_path / "routines").mkdir()
    return scripted


def write(home: Path, name: str, text: str) -> Path:
    path = home / "routines" / f"{name}.md"
    path.write_text(text)
    return path


STANDUP = """---
bot: chief
schedule: "0 9 * * 1-5"
---
Standup since {{since}}.

{{activity}}
"""


# Schedules.


def test_cron_matches_weekdays_names_and_steps():
    weekdays = Cron.parse("0 9 * * mon-fri")
    assert weekdays.matches(MONDAY_9) and not weekdays.matches(at(25, 9))  # Sunday
    assert not weekdays.matches(at(26, 9, 1))
    every_15 = Cron.parse("*/15 * * * *")
    assert [m for m in range(60) if every_15.matches(at(26, 3, m))] == [0, 15, 30, 45]
    assert Cron.parse("@hourly").matches(at(26, 14)) and Cron.parse("0 0 * * 7").matches(at(25, 0))


def test_cron_with_a_day_and_a_weekday_runs_on_either_like_cron():
    first_or_friday = Cron.parse("0 12 1 * fri")
    assert first_or_friday.matches(at(1, 12)) and first_or_friday.matches(at(30, 12))  # the 1st; a Friday
    assert not first_or_friday.matches(at(29, 12))


@pytest.mark.parametrize("bad", ["0 9 * *", "61 * * * *", "0 9 * * funday", "*/0 * * * *", "5-1 * * * *"])
def test_bad_schedules_say_what_is_wrong(bad):
    with pytest.raises(RoutineError, match="schedule"):
        Cron.parse(bad)


def test_a_week_away_still_runs_a_routine_only_once():
    cron = Cron.parse("0 9 * * *")
    asleep, awake = at(19, 23), at(26, 10, 30)

    assert cron.due(asleep, awake) == MONDAY_9  # the latest one missed, not one per day
    assert cron.due(MONDAY_9, awake) is None  # and it was already run
    assert cron.next(at(26, 10)) == at(27, 9)


# Routine files.


def test_routine_files_load_and_one_broken_file_doesnt_stop_the_rest(tmp_path):
    (tmp_path / "routines").mkdir()
    write(tmp_path, "standup", STANDUP)
    write(tmp_path, "broken", "---\nbot: chief\nschedule: often\n---\nx")
    write(tmp_path, "stranger", "---\nbot: nobody\nschedule: '@daily'\n---\nx")

    loaded, errors = routines.load(tmp_path / "routines", {"chief"})

    assert [r.name for r in loaded] == ["standup"]
    assert "cron has five fields" in errors["broken.md"] and "no bot named 'nobody'" in errors["stranger.md"]


def test_on_is_read_as_a_trigger_not_as_yaml_true():
    r = routines.parse("---\nbot: reviewer\non: github.review_requested\nrepo: o/r\n---\nReview {{pr.url}}", "x", Path("x"))

    assert r.on == "github.review_requested" and r.when == "on github.review_requested in o/r"


@pytest.mark.parametrize("text, error", [
    ("---\nbot: chief\n---\nx", "either a schedule or an on trigger"),
    ("---\nbot: chief\nschedule: '@daily'\non: github.ci_failed\n---\nx", "either a schedule"),
    ("---\nbot: chief\non: github.ci_failed\n---\nx", "needs a repo"),
    ("---\nbot: chief\nschedule: '@daily'\n---\n{{pr.url}}", "can't be filled in here"),
    ("---\nbot: chief\non: github.review_requested\nrepo: o/r\n---\n{{pr.failures}}", "can't be filled in here"),
    ("---\nbot: chief\nschedule: '@daily'\nbudget: lots\n---\nx", "budget"),
    ("---\nbot: chief\nschedule: '@daily'\nbudget: 5000\n---\nx", "per-run budget is for triggers"),
    ("---\nbot: chief\nschedule: '@daily'\nwhen: now\n---\nx", "unknown fields"),
])
def test_routine_mistakes_are_caught_when_the_file_is_read(text, error):
    with pytest.raises(RoutineError, match=error):
        routines.parse(text, "x", Path("x"))


def test_the_starter_routines_all_load_and_a_new_crew_has_them(tmp_path):
    for name, text in routines.starters().items():
        routines.parse(text, name, Path(name))
    home = crew_module.init(tmp_path / "crew")

    loaded, errors = routines.load(home / "routines", set(crew_module.load(home).bots))

    assert not errors
    assert {r.name: r.enabled for r in loaded} == {"standup": True, "retro": True, "review-requests": False,
                                                   "ci-failed": False, "verification": True}


def test_adding_a_starter_routine_to_an_existing_crew(tmp_path):
    path = routines.add_starter(tmp_path, "standup")

    assert path == tmp_path / "routines" / "standup.md" and "{{activity}}" in path.read_text()
    with pytest.raises(RoutineError, match="already exists"):
        routines.add_starter(tmp_path, "standup")
    with pytest.raises(RoutineError, match="no starter routine"):
        routines.add_starter(tmp_path, "nightly")


# The scheduler.


async def test_a_new_routine_waits_for_its_next_time_then_runs_once(crew, store, tmp_path):
    write(tmp_path, "standup", STANDUP)
    scheduler = Scheduler(crew.runner)

    assert await scheduler.tick(at(26, 8, 59)) == []  # first seen: nothing runs for times before it existed
    [task] = await scheduler.tick(at(26, 9, 0))
    assert await scheduler.tick(at(26, 9, 0)) == []

    assert task.bot == "chief" and task.kind == "chat"  # a message in the chief's chat, from the routine
    await wait_until(crew.settled(task.id))
    [spec] = crew.specs_for("chief")
    assert "ongoing chat with the user" in spec.system_prompt
    # The first run looks back to the time it would last have run.
    assert spec.prompt.startswith("[routine: standup]\n\nStandup since Fri 23 Oct 09:00.")
    assert [e.data["from"] for e in store.events(task.id) if e.kind == "message"] == ["routine:standup"]
    assert store.routine_state("standup")["last_task_id"] == task.id


async def test_routine_during_cancel_is_delivered_after_cleanup(crew, store, tmp_path, monkeypatch):
    write(tmp_path, "standup", STANDUP)
    scheduler = Scheduler(crew.runner)
    await scheduler.tick(at(26, 8, 59))
    chat = crew.runner.chat("chief")
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_close(_task_id):
        entered.set()
        await release.wait()

    monkeypatch.setattr(crew.runner.browser, "close_task", blocked_close)
    cancelling = asyncio.create_task(crew.runner.cancel(chat.id))
    await asyncio.wait_for(entered.wait(), 2)
    [task] = await scheduler.tick(at(26, 9))
    assert task.id == chat.id and not crew.specs_for("chief")
    assert any("[routine: standup]" in text for text in crew.runner.queued(chat.id))
    release.set()
    await cancelling
    await wait_until(crew.settled(chat.id))
    assert any("[routine: standup]" in spec.prompt for spec in crew.specs_for("chief"))
    assert store.routine_state("standup")["last_task_id"] == chat.id


async def test_a_routine_still_waiting_in_a_busy_chat_is_skipped_rather_than_doubled(store, tmp_path):
    gate = asyncio.Event()
    busy = ScriptedCrew(store, tmp_path, bot("chief"), gate=gate)
    (tmp_path / "routines").mkdir()
    write(tmp_path, "hourly", "---\nbot: chief\nschedule: '0 * * * *'\n---\nCheck in.")
    scheduler = Scheduler(busy.runner)
    await scheduler.tick(at(26, 8, 30))
    chat = busy.runner.say("chief", "a long question")  # the chief is busy with you

    [first] = await scheduler.tick(at(26, 9))  # waits behind your question
    assert first.id == chat.id and await scheduler.tick(at(26, 10)) == []
    assert scheduler.views(at(26, 10))[0].skipped_at == at(26, 10).isoformat()
    gate.set()
    await wait_until(busy.settled(chat.id))


async def test_a_paused_crew_runs_what_came_due_once_it_resumes(crew, tmp_path):
    write(tmp_path, "standup", STANDUP)
    scheduler = Scheduler(crew.runner)
    await scheduler.tick(at(26, 8))
    crew.runner.pause()

    assert await scheduler.tick(at(26, 9)) == []
    crew.runner.resume()
    assert len(await scheduler.tick(at(26, 11))) == 1


async def test_only_a_scheduled_routine_can_be_run_by_hand(crew, store, tmp_path):
    write(tmp_path, "ci", "---\nbot: chief\non: github.ci_failed\nrepo: vir/app\n---\nFix {{pr.url}}")
    scheduler = Scheduler(crew.runner)

    with pytest.raises(RoutineError, match="can't be run by hand"):
        scheduler.run_now("ci")
    with pytest.raises(RoutineError, match="no routine named"):
        scheduler.run_now("nightly")
    assert store.list_tasks() == []


def test_routines_over_the_api(crew, tmp_path):
    write(tmp_path, "standup", STANDUP)
    write(tmp_path, "bad", "---\nbot: chief\n---\nx")
    auth = {"Authorization": "Bearer t"}
    with TestClient(create_app(crew.runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        listing = client.get("/routines", headers=auth).json()
        assert [r["name"] for r in listing["routines"]] == ["standup"] and "bad.md" in listing["errors"]
        assert listing["routines"][0]["next_run_at"] is not None

        task = client.post("/routines/standup/run", headers=auth).json()
        assert task["kind"] == "chat" and task["bot"] == "chief"
        assert client.post("/routines/nope/run", headers=auth).status_code == 422

        starters = [s["name"] for s in listing["starters"]]
        assert "standup" not in starters and "retro" in starters  # standup.md is already there
        assert client.post("/routines/starters/retro", headers=auth).json()["schedule"] == "0 16 * * 5"

        assert "verification" not in starters  # it's for eng-lead, who isn't in this crew
        refused = client.post("/routines/starters/verification", headers=auth)
        assert refused.status_code == 422 and "no bot named 'eng-lead'" in refused.json()["detail"]
        assert not (tmp_path / "routines" / "verification.md").exists()


async def test_the_activity_a_routine_reads(crew, store, tmp_path):
    since = datetime.now().astimezone() - timedelta(minutes=1)
    goal = crew.runner.submit("chief", "Add CSV export")
    await wait_until(crew.settled(goal.id))
    crew.runner.send(goal.id, "Use semicolons, not commas")
    await wait_until(crew.settled(goal.id))
    review = store.create_task("reviewer", "Review the export", parent_id=goal.id, created_by="chief")
    store.append_event(review.id, kind="review", data={
        "reviewer": "reviewer", "version": 1, "verdict": Verdict.FAIL, "summary": "Quotes aren't escaped",
        "findings": [{"location": "export.py:12", "problem": "a comma in a name splits the row"}]})
    earlier_standup = store.create_task("chief", "Standup", routine="standup")

    text = activity(store, since, exclude_routine="standup")

    assert "## Add CSV export" in text and f"delegated {review.id} to reviewer" in text
    assert "you said: Use semicolons, not commas" in text
    assert "reviewed version 1: FAIL, Quotes aren't escaped Findings: export.py:12" in text
    assert "Standup" not in text and earlier_standup.id not in text
    assert activity(store, datetime.now().astimezone() + timedelta(minutes=1)) == "Nothing happened in the crew in that time."


async def test_the_retro_reads_what_went_wrong_counting_each_finding_once(crew, store, tmp_path):
    from crewbots.crew_tools import CrewTools

    since = datetime.now().astimezone() - timedelta(minutes=1)
    assert corrections(store, since).startswith("No bot noted a correction")
    goal = store.create_task("chief", "Add CSV export")
    tools = CrewTools(crew.runner, goal.id, can_delegate=True)
    first = await tools.call("remember", {"title": "Exports use semicolons", "body": "The user's sheets are German.",
                                          "mistake": "Used commas in the CSV export", "check": "a test reading the header"})
    assert "Counted as a correction" in first and "earlier" not in first
    again = await tools.call("remember", {"title": "Quote names", "body": "Names have commas.", "mistake": "Unquoted"})
    assert "Your earlier ones, the latest first: Exports use semicolons." in again
    plain = await tools.call("remember", {"title": "Lunch is at one", "body": "The user eats at one."})
    assert "correction" not in plain
    work = store.create_task("reviewer", "Build the export", parent_id=goal.id, created_by="chief")
    review = store.create_task("reviewer", "Review the export", parent_id=goal.id, created_by="chief")
    verdict = {"work_id": work.id, "reviewer": "reviewer", "version": 2, "verdict": "fail", "summary": "Quotes",
               "findings": [{"location": "export.py:12", "problem": "a comma in a name splits the row"}]}
    store.append_event(review.id, kind="review", data=verdict)  # recorded on the review and on the work
    store.append_event(work.id, kind="review", data=verdict)

    text = corrections(store, since)

    assert "## Corrections the user made (2)" in text
    assert (f"in {goal.id}: Used commas in the CSV export Lesson: The user's sheets are German. "
            "A check that would catch it: a test reading the header") in text
    assert "Lunch" not in text
    assert f"## Review findings (1)\nIn reviewer's work:\n- {work.id} version 2, by reviewer: export.py:12" in text


# GitHub triggers.


def pr(number=7, head="a1", repo="vir/app", failed=()):
    return PullRequest(repo=repo, number=number, title=f"PR {number}", url=f"https://github.com/{repo}/pull/{number}",
                       author="sam", branch=f"feature-{number}", head=head, checks="FAILURE" if failed else "SUCCESS",
                       failed=failed)


class FakeGitHub:
    def __init__(self):
        self.snapshot = Snapshot(review_requested=[], mine=[])
        self.diffs: list[int] = []
        self.error: Exception | None = None

    def fetch(self):
        if self.error:
            raise self.error
        return self.snapshot

    def diff(self, p):
        self.diffs.append(p.number)
        return f"diff of {p.number}"

    def failures(self, p):
        return "\n".join(name for name, _ in p.failed)


REVIEW = """---
bot: reviewer
on: github.review_requested
repo: vir/app
---
Review {{pr.repo}}#{{pr.number}} by {{pr.author}}: {{pr.title}}
{{pr.diff}}
"""


async def test_a_trigger_starts_on_whats_new_once_per_version(crew, tmp_path):
    write(tmp_path, "reviews", REVIEW)
    gh = FakeGitHub()
    gh.snapshot = Snapshot(review_requested=[pr(1), pr(2, repo="someone/else")], mine=[])
    scheduler = Scheduler(crew.runner, github=gh, github_every_s=0)

    assert await scheduler.tick(at(26, 9)) == []  # what was already waiting isn't new
    gh.snapshot = Snapshot(review_requested=[pr(1), pr(3), pr(4, repo="someone/else")], mine=[])
    [task] = await scheduler.tick(at(26, 9, 5))
    assert await scheduler.tick(at(26, 9, 10)) == []
    gh.snapshot = Snapshot(review_requested=[pr(1), pr(3, head="b2")], mine=[])  # a new push to #3
    [again] = await scheduler.tick(at(26, 9, 15))

    assert task.bot == "reviewer" and task.title == "Reviews: vir/app#3 PR 3"
    await wait_until(crew.settled(task.id))
    assert "diff of 3" in crew.specs_for("reviewer")[0].prompt
    assert again.title == task.title and gh.diffs == [3, 3]


async def test_failed_checks_start_the_ci_trigger_with_the_failures(crew, tmp_path):
    write(tmp_path, "ci", "---\nbot: chief\non: github.ci_failed\nrepo: vir/app\n---\nFix {{pr.branch}}:\n{{pr.failures}}")
    gh = FakeGitHub()
    scheduler = Scheduler(crew.runner, github=gh, github_every_s=0)
    await scheduler.tick(at(26, 9))
    gh.snapshot = Snapshot(review_requested=[], mine=[pr(5), pr(6, failed=(("tests", None), ("lint", None)))])

    [task] = await scheduler.tick(at(26, 9, 5))

    await wait_until(crew.settled(task.id))
    assert crew.specs_for("chief")[0].prompt == ('Fix <untrusted source="branch name">\nfeature-6\n</untrusted>:\n'
                                                 '<untrusted source="failed checks">\ntests\nlint\n</untrusted>')


async def test_a_flood_of_new_pull_requests_starts_a_few_at_a_time(crew, tmp_path):
    write(tmp_path, "reviews", REVIEW)
    gh = FakeGitHub()
    scheduler = Scheduler(crew.runner, github=gh, github_every_s=0)
    await scheduler.tick(at(26, 9))
    gh.snapshot = Snapshot(review_requested=[pr(n) for n in range(10, 20)], mine=[])

    assert len(await scheduler.tick(at(26, 9, 5))) == MAX_STARTS_PER_POLL
    assert len(await scheduler.tick(at(26, 9, 10))) == MAX_STARTS_PER_POLL


async def test_github_being_unreachable_is_reported_not_fatal(crew, tmp_path):
    write(tmp_path, "reviews", REVIEW)
    gh = FakeGitHub()
    gh.error = RuntimeError("gh: not logged in")
    scheduler = Scheduler(crew.runner, github=gh, github_every_s=0)

    assert await scheduler.tick(at(26, 9)) == []
    assert scheduler.github_error == "gh: not logged in"


def test_pull_requests_are_read_from_githubs_answer():
    node = {"number": 4, "title": "Fix it", "url": "u", "headRefName": "fix", "headRefOid": "abc",
            "author": {"login": "sam"}, "repository": {"nameWithOwner": "vir/app"},
            "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE", "contexts": {"nodes": [
                {"__typename": "CheckRun", "name": "tests", "conclusion": "FAILURE",
                 "detailsUrl": "https://github.com/vir/app/actions/runs/1/job/2"},
                {"__typename": "CheckRun", "name": "lint", "conclusion": "SUCCESS", "detailsUrl": None},
                {"__typename": "StatusContext", "context": "deploy", "state": "ERROR", "targetUrl": "d"},
            ]}}}}]}}

    [p] = _prs({"nodes": [node, {}]})

    assert p.key == "vir/app#4@abc" and p.checks == "FAILURE"
    assert p.failed == (("tests", "https://github.com/vir/app/actions/runs/1/job/2"), ("deploy", "d"))


async def test_a_triggers_thread_is_held_to_its_budget(store, tmp_path):
    # The trigger's budget covers the thread it opens: here, 12 tokens a turn and a budget of 20.
    scripted = ScriptedCrew(store, tmp_path, bot("chief"))
    (tmp_path / "routines").mkdir()
    write(tmp_path, "ci", "---\nbot: chief\non: github.ci_failed\nrepo: vir/app\nbudget: 20\n---\nFix {{pr.url}}")
    scripted.script["chief"] = lambda spec, me: [*reply(spec)[1:3], Done(final_text="ok")]  # text, usage, done
    gh = FakeGitHub()
    scheduler = Scheduler(scripted.runner, github=gh, github_every_s=0)
    await scheduler.tick(at(26, 9))
    gh.snapshot = Snapshot(review_requested=[], mine=[pr(9, failed=(("tests", None),))])
    [task] = await scheduler.tick(at(26, 9, 5))
    assert task.kind == "thread" and task.parent_id == scripted.runner.chat("chief").id
    await wait_until(scripted.settled(task.id))
    scripted.runner.send(task.id, "and again")
    await wait_until(lambda: store.get_task(task.id).status == "idle")
    scripted.runner.send(task.id, "once more")

    await wait_until(lambda: store.get_task(task.id).status == "held")
    assert "used 24 of its 20 tokens" in [e for e in store.events(task.id) if e.kind == "held"][0].data["reason"]
    await scripted.runner.cancel(task.id)


# The crew's own files.


async def test_edited_role_cards_count_without_a_reload(store, tmp_path):
    home = crew_module.init(tmp_path / "crew")
    runner = Runner(store, crew_module.load(home), {})
    scheduler = Scheduler(runner)
    card = home / "bots" / "engineer-1.md"
    assert runner.crew.bots["engineer-1"].network is False and runner.crew.bots["engineer-1"].path == card

    card.write_text(card.read_text().replace("access: workspace_write\n", "access: workspace_write\nnetwork: true\n"))
    os_utime_later(card)
    assert scheduler.reload_if_changed() and runner.crew.bots["engineer-1"].network is True

    card.write_text(card.read_text().replace("network: true\n", "network: everywhere\n"))
    os_utime_later(card)
    assert not scheduler.reload_if_changed()
    assert "network is true, or a list of hosts" in scheduler.crew_error
    assert runner.crew.bots["engineer-1"].network is True  # the crew carries on as it was


def os_utime_later(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
