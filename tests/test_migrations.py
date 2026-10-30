"""The store's numbered migrations: an old crew's database comes up to date once, keeping everything it had."""

import sqlite3
from pathlib import Path

import pytest

from crewbots.store import Status, Store
from crewbots.store import db as db_module

OLD = Path(__file__).parent / "fixtures" / "crew-2026-10-25.sql"


def old_database(path: Path) -> Path:
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD.read_text())
    return path


def layout(store: Store) -> dict[str, object]:
    """Each table's and index's columns, by name: what a database is laid out as, ignoring column order."""
    out = {}
    for row in store.db.execute("select type, name from sqlite_master where type in ('table', 'index') "
                                "and name not like 'sqlite_%' and name not like 'search_%'"):
        if row["type"] == "table":
            out[row["name"]] = sorted(r["name"] for r in store.db.execute(f"pragma table_info({row['name']})"))
        else:
            out[row["name"]] = sorted(r["name"] for r in store.db.execute(f"pragma index_info({row['name']})"))
    return out


def test_an_old_crews_database_comes_up_to_date_and_keeps_its_history(tmp_path):
    store = Store(old_database(tmp_path / "crewbots.db"))

    assert store.db.execute("pragma user_version").fetchone()[0] == len(db_module.MIGRATIONS)
    chief, engineer = store.get_task("t_0000000001"), store.get_task("t_0000000002")
    assert chief.status is Status.IDLE and chief.kind == "thread" and chief.turns == 0  # new columns, defaults
    assert engineer.parent_id == chief.id and engineer.result == "Done: --verbose prints each step."
    assert [e.kind for e in store.events(engineer.id)] == ["text", "usage"]
    assert store.search("verbose flag")[0]["task_id"] in {chief.id, engineer.id}  # the old history was indexed
    assert store.search("rbose")[0]["task_id"] in {chief.id, engineer.id}  # by trigrams too
    room = store.create_room("Crew", "chief", ["chief", "engineer"])  # and what came later works on it
    store.add_room_message(room["id"], "user", "hello", attachments=[{"id": "a1"}])
    store.close()


def test_a_migrated_database_is_laid_out_like_a_new_one(tmp_path):
    migrated = Store(old_database(tmp_path / "old.db"))
    fresh = Store(tmp_path / "new.db")

    assert layout(migrated) == layout(fresh)


def test_each_step_runs_once(tmp_path, monkeypatch):
    path = old_database(tmp_path / "crewbots.db")
    Store(path).close()
    ran = []
    monkeypatch.setattr(db_module, "MIGRATIONS", [*db_module.MIGRATIONS,
                                                  ("a later change", lambda db, index: ran.append(1))])

    Store(path).close()
    Store(path).close()

    assert ran == [1]  # once, though the database was opened twice
    assert Store(path).db.execute("pragma user_version").fetchone()[0] == len(db_module.MIGRATIONS)


def test_a_new_database_starts_with_every_step_done(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(db_module, "MIGRATIONS", [("only for old databases", lambda db, index: ran.append(1))])

    store = Store(tmp_path / "crewbots.db")

    assert ran == [] and store.db.execute("pragma user_version").fetchone()[0] == 1


def test_previous_version_gains_persistent_direct_chat_mutes(tmp_path):
    path = tmp_path / "crewbots.db"
    store = Store(path)
    store.db.execute("drop table chat_mutes")
    before_mutes = next(i for i, (name, _) in enumerate(db_module.MIGRATIONS) if name == "direct chat mute settings")
    store.db.execute(f"pragma user_version = {before_mutes}")
    store.close()

    migrated = Store(path)
    assert migrated.db.execute("pragma user_version").fetchone()[0] == len(db_module.MIGRATIONS)
    assert not migrated.chat_muted("chief")
    migrated.set_chat_muted("chief", True)
    migrated.close()

    reopened = Store(path)
    assert reopened.chat_muted("chief")
    reopened.set_chat_muted("chief", False)
    assert not reopened.chat_muted("chief")
    reopened.close()


def test_previous_version_gains_merged_tip_without_changing_tasks(tmp_path):
    path = tmp_path / "crew.db"
    store = Store(path)
    task = store.create_task("worker", "merged task")
    store.update_task(task.id, merge_state="merged", merged_commit="old-merge")
    store.db.execute("alter table tasks drop column merged_tip")
    before = [name for name, _ in db_module.MIGRATIONS].index("remember a merged task's branch tip")
    store.db.execute(f"pragma user_version = {before}")
    store.close()

    migrated = Store(path)
    result = migrated.get_task(task.id)
    assert result.merged_commit == "old-merge" and result.merged_tip is None
    migrated.update_task(task.id, merged_tip="tip")
    assert migrated.get_task(task.id).merged_tip == "tip"
    migrated.close()


def test_an_older_crews_room_called_crew_becomes_its_own(tmp_path):
    path = tmp_path / "crew.db"
    store = Store(path)
    crew_room = store.create_room("Crew", "chief", ["chief"])
    store.create_room("Crew", "chief", ["chief"])  # a later one with the same name stays an ordinary group chat
    store.db.execute("update rooms set crew = 0")
    store.db.execute("pragma user_version = 2")  # from before rooms were marked
    store.close()

    again = Store(path)
    assert [r["id"] for r in again.rooms() if r["crew"]] == [crew_room["id"]]
    again.close()


def test_a_database_from_before_numbering_but_up_to_date_is_left_as_it_was(tmp_path):
    path = tmp_path / "crewbots.db"
    store = Store(path)
    task = store.create_task("worker", "already here")
    store.db.execute("pragma user_version = 0")  # as every crew's database was until migrations were numbered
    store.close()

    again = Store(path)

    assert again.get_task(task.id).title == "already here"
    assert len(again.search("already")) == 1  # not indexed twice
    assert again.db.execute("pragma user_version").fetchone()[0] == len(db_module.MIGRATIONS)


@pytest.mark.parametrize("table", ["tasks", "room_messages"])
def test_every_column_added_before_numbering_is_in_todays_layout(tmp_path, table):
    columns = {r["name"] for r in Store(tmp_path / "x.db").db.execute(f"pragma table_info({table})")}
    assert set(db_module.ADDED_COLUMNS[table]) <= columns
