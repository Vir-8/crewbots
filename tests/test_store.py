import pytest

from crewbots.events import Text
from crewbots.store import Status, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def test_a_new_task_is_queued_with_a_short_title(store):
    task = store.create_task("engineer", "Fix the login bug\n\nIt fails when the password has a space.")

    assert task.status is Status.QUEUED
    assert task.title == "Fix the login bug"
    assert task.created_by == "user"
    assert store.get_task(task.id) == task


def test_long_titles_are_cut(store):
    assert len(store.create_task("engineer", "x" * 500).title) == 80


def test_tasks_filter_by_status_and_bot(store):
    a = store.create_task("engineer", "a")
    store.create_task("reviewer", "b")
    store.update_task(a.id, status=Status.IDLE)

    assert [t.id for t in store.list_tasks(status=Status.IDLE)] == [a.id]
    assert [t.id for t in store.list_tasks(bot="engineer")] == [a.id]


def test_the_owner_of_a_task_never_changes(store):
    task = store.create_task("engineer", "a")

    with pytest.raises(ValueError, match="bot"):
        store.update_task(task.id, bot="reviewer")


def test_usage_adds_up(store):
    task = store.create_task("engineer", "a")
    store.add_usage(task.id, 100, 10, 0.5)
    store.add_usage(task.id, 50, 5, None)

    task = store.get_task(task.id)
    assert (task.input_tokens, task.output_tokens, task.cost_usd) == (150, 15, 0.5)


def test_events_come_back_in_order_and_from_a_cursor(store):
    a = store.create_task("engineer", "a")
    b = store.create_task("engineer", "b")
    first = store.append_event(a.id, Text(text="one"))
    store.append_event(b.id, kind="message", data={"from": "user", "text": "hi"})
    store.append_event(a.id, Text(text="two"))

    assert [e.data["text"] for e in store.events(a.id)] == ["one", "two"]
    assert [e.data["text"] for e in store.events(a.id, after=first.id)] == ["two"]
    assert [e.kind for e in store.events()] == ["text", "message", "text"]


def test_recent_events_are_the_newest_in_order_and_can_be_filtered(store):
    a = store.create_task("engineer", "a")
    for n in range(5):
        store.append_event(a.id, Text(text=str(n)))
    store.append_event(a.id, kind="handoff", data={"done": "x"})

    assert [e.data.get("text") for e in store.recent_events(3)] == ["3", "4", None]
    assert [e.kind for e in store.recent_events(10, ("handoff",))] == ["handoff"]


def test_unfinished_tasks_are_marked_interrupted_on_startup(store):
    running = store.create_task("engineer", "a")
    store.update_task(running.id, status=Status.RUNNING)
    idle = store.create_task("engineer", "b")
    store.update_task(idle.id, status=Status.IDLE)

    assert store.mark_interrupted() == [running.id]
    assert store.get_task(running.id).status is Status.INTERRUPTED
    assert store.get_task(idle.id).status is Status.IDLE


def test_a_missing_task_is_a_key_error(store):
    with pytest.raises(KeyError):
        store.get_task("t_nope")
    with pytest.raises(KeyError):
        store.update_task("t_nope", status=Status.IDLE)
