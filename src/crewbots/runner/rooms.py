"""Group chats: posting and waking mentioned bots, the lead, or @everyone."""

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

from crewbots import attachments as attachments_module
from crewbots import prompts, room_files, timefmt, working_state
from crewbots.crew import is_no_reply
from crewbots.runner.common import NotAllowed, UnknownBot, _iso
from crewbots.store import Status, Task

CREW_ROOM = "Crew"  # the group chat every bot is in, made when the crew first needs one
MENTION = re.compile(r"@([\w-]+)")
EVERYONE = "everyone"  # @everyone wakes every bot in the room
MAX_ROOM_BOTS = 8  # bots in one group chat: more and they talk past each other
CATCH_UP = 12000  # characters of what a woken bot missed in a group chat: the newest, with older messages left out


def _safe_error(error: str) -> str:
    """Mask common credential forms before limiting error text returned to a bot."""
    error = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)",
                   "[redacted private key]", error)
    error = re.sub(r"(?i)(bearer\s+)[^\s,;\"']+", r"\1[redacted]", error)
    error = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+@", r"\1[redacted]@", error)
    key = r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|authorization)"
    error = re.sub(rf'''(?i)({key}["']?\s*[:=]\s*)(["'])(.*?)(\2)''', r"\1\2[redacted]\4", error)
    error = re.sub(rf'''(?i)({key}["']?\s*[:=]\s*)([^\s,;"'&}}]+)''', r"\1[redacted]", error)
    error = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|AKIA[A-Z0-9]{16})\b",
                   "[redacted]", error)
    return error[:1000] + ("…" if len(error) > 1000 else "")


class Rooms:
    """The part of Runner that runs group chats."""

    def room_status(self, task_id: str, room_id: str | None = None) -> dict[str, Any]:
        bot = self.store.get_task(task_id).bot
        if not self.is_coordinator(bot):
            raise NotAllowed("only coordinators can inspect room seats")
        rooms = self.store.rooms()
        if room_id:
            rooms = [room for room in rooms if room["id"] == room_id and bot in room["members"]]
            if not rooms:
                raise NotAllowed("room not found or you are not a member")
        else:
            rooms = [room for room in rooms if bot in room["members"]]
        result = []
        for room in rooms[:50]:
            seats = self.store.seats_in(room["id"])
            entries = []
            for seat in seats[:50]:
                row = self.store.db.execute(
                    "select json_extract(data, '$.error') from events where task_id = ? and kind = 'done'"
                    " and json_extract(data, '$.is_error') = 1 and json_extract(data, '$.error') is not null"
                    " and json_extract(data, '$.error') != '' and id > coalesce((select max(id) from events"
                    " where task_id = ? and kind = 'done' and json_extract(data, '$.is_error') = 0), 0)"
                    " order by id desc limit 1", (seat.id, seat.id)).fetchone()  # none once a later turn succeeded
                error = seat.error or (row[0] if row else None)
                attempt = dict(self._attempts.get(seat.id, {}))
                last_activity = attempt.pop("last_activity", None)
                if last_activity is not None and time.monotonic() - last_activity >= self.stall_s:
                    attempt["state"] = "stalled"
                entries.append({"task_id": seat.id, "bot": seat.bot, "status": seat.status.value,
                                "updated_at": timefmt.minute(seat.updated_at),
                                "error": _safe_error(error) if error else None,
                                "attempt": attempt or None})
            result.append({"room_id": room["id"], "title": room["title"][:200], "seats": entries,
                           "seats_truncated": len(seats) > 50})
        return {"rooms": result, "rooms_truncated": len(rooms) > 50}

    def crew_room(self) -> dict[str, Any]:
        """The group chat with every bot in it, led by the coordinator."""
        for room in self.store.rooms():
            if room["crew"]:
                return room
        bots = list(self.crew.bots.values())
        chief = self.coordinator()
        if chief is None:
            raise NotAllowed("the configured chief is missing; restore it or change the crew's chief setting")
        lead = chief.name
        return self.create_room(CREW_ROOM, [b.name for b in bots], lead, crew=True)

    def make_standing_rooms(self) -> list[dict[str, Any]]:
        """The group chats in the crew's `rooms/` folder that haven't been made yet, made. Each is made once: what the
        user changes or deletes afterwards stays that way."""
        files, errors = room_files.load(self.crew.home / "rooms", set(self.crew.bots))
        for name, why in errors.items():
            logging.getLogger(__name__).warning("couldn't make the group chat in rooms/%s: %s", name, why)
        made, new = self.store.rooms_made(), []
        for file in [f for f in files if f.name not in made]:
            try:
                room = self.create_room(file.title, file.members, file.lead)
            except Exception:
                logging.getLogger(__name__).exception("couldn't make the group chat in rooms/%s.md", file.name)
                continue
            made[file.name] = room["id"]
            self.store.set_rooms_made(made)
            new.append(room)
        return new

    def room_about(self, room_id: str) -> str:
        """What a group chat made from the crew's files is for, as its file says now."""
        name = next((n for n, made in self.store.rooms_made().items() if made == room_id), None)
        if name is None:
            return ""
        try:
            path = self.crew.home / "rooms" / f"{name}.md"
            return room_files.parse(path.read_text(), name, set(self.crew.bots)).about
        except (OSError, room_files.RoomFileError):
            return ""

    def create_room(self, title: str, members: list[str], lead: str | None = None, crew: bool = False) -> dict[str, Any]:
        unknown = [m for m in members if m not in self.crew.bots]
        if unknown or not members:
            raise UnknownBot(f"no bots named {unknown}; the crew is {sorted(self.crew.bots)}" if unknown
                             else "a group chat needs at least one bot")
        lead = lead or members[0]
        if lead not in members:
            raise UnknownBot(f"the lead, {lead}, isn't in the group chat")
        room = self.store.create_room(title.strip() or ", ".join(members), lead, members, crew)
        self.bus.publish({"type": "room", "room": room})
        return room

    async def change_room(self, room_id: str, title: str | None = None, lead: str | None = None,
                          members: list[str] | None = None, muted: bool | None = None,
                          archived: bool | None = None) -> dict[str, Any]:
        """The user's changes to a group chat; what's left None stays. Its bots take up a new name, lead or members
        from their next turn there, which is told the room's setting again. Archived, it's kept but out of the way,
        and its bots aren't woken there."""
        room = self.store.room(room_id)
        changes: dict[str, Any] = {}
        if title is not None:
            if not title.strip():
                raise NotAllowed("a group chat needs a name")
            changes["title"] = title.strip()
        if members is not None:
            if unknown := [m for m in members if m not in self.crew.bots]:
                raise UnknownBot(f"no bots named {unknown}; the crew is {sorted(self.crew.bots)}")
            if not members:
                raise NotAllowed("a group chat needs at least one bot")
            changes["members"] = list(dict.fromkeys(members))
        if lead is not None or room["lead"] not in changes.get("members", room["members"]):
            if (lead or room["lead"]) not in changes.get("members", room["members"]):
                raise NotAllowed(f"{lead or room['lead']} isn't in the group chat: its lead is one of its bots")
            changes["lead"] = lead or room["lead"]
        if muted is not None:
            changes["muted"] = int(muted)
        if archived is not None:
            if archived and room["crew"]:
                raise NotAllowed("the crew's own group chat can't be archived")
            changes["archived_at"] = _iso(datetime.now(UTC)) if archived else None
        room = self._set_room(room_id, **changes)
        if "title" in changes:  # each bot's next turn there is told the room as it is now
            for seat in self.store.seats_in(room_id):
                self._set(seat.id, title=f"{room['title']} ({seat.bot})")
        return room

    async def delete_room(self, room_id: str) -> None:
        """Deletes a group chat and what was said in it. Work it handed out carries on, and reports to each bot's own
        chat instead."""
        room = self.store.room(room_id)
        if room["crew"]:
            raise NotAllowed("the crew's own group chat can't be deleted")
        seats = self.store.seats_in(room_id)
        if any(seat.id in self._cancelling for seat in seats):
            raise NotAllowed("A seat in this group chat is already being cancelled.")
        try:
            for seat in seats:
                self._begin_cancel(seat.id)
                for child in self.store.children(seat.id):
                    self._set(child.id, parent_id=self.chat(seat.bot).id)
            for seat in seats:
                await self.cancel(seat.id, _begun=True)
                self._seat_depth.pop(seat.id, None)
            self.store.delete_room(room_id)
            for seat in seats:
                self._cancel_messages.pop(seat.id, None)
            for bot in set(self.crew.bots) | set(room["members"]) | {seat.bot for seat in seats}:
                self._clear_room_mentions(bot, room_id)
            self.bus.publish({"type": "room_gone", "room_id": room_id})
        finally:
            self._cancelling.difference_update(seat.id for seat in seats)

    def _join_crew_room(self, bots: set[str]) -> None:
        """Bots new to the crew join its own group chat, if it has one yet."""
        room = next((r for r in self.store.rooms() if r["crew"]), None)
        if room and (joining := [b for b in self.crew.bots if b in bots and b not in room["members"]]):
            self._set_room(room["id"], members=[*room["members"], *joining])

    def open_group(self, task_id: str, title: str, members: list[str], lead: str | None = None) -> dict[str, Any]:
        """The `open_group` tool: a bot opens a group chat with teammates. It's in it, and leads it unless it names
        another lead. The app shows it at once."""
        task = self.store.get_task(task_id)
        if task.kind == "ask":
            raise NotAllowed("you're answering a teammate's question: answer it, rather than starting more")
        members = list(dict.fromkeys([task.bot, *members]))
        if len(members) < 2:
            raise NotAllowed("a group chat needs at least one teammate besides you")
        if len(members) > MAX_ROOM_BOTS:
            raise NotAllowed(f"a group chat holds at most {MAX_ROOM_BOTS} bots, so they don't talk past each other")
        room = self.create_room(title, members, lead or task.bot)
        self.store.log_agent("open_group", task.bot, room_id=room["id"], text=f"{room['title']}: {', '.join(members)}")
        return room

    def add_to_group(self, task_id: str, bot: str, room_id: str | None = None) -> dict[str, Any]:
        """The `add_to_group` tool: a bot in a group chat brings a teammate in (by default into the one it's in)."""
        task = self.store.get_task(task_id)
        room_id = room_id or (task.room_id if task.kind == "seat" else None)
        if not room_id:
            raise NotAllowed("say which group chat (room): you're not talking in one")
        try:
            room = self.store.room(room_id)
        except KeyError:
            raise NotAllowed(f"no group chat {room_id}") from None
        if task.bot not in room["members"]:
            raise NotAllowed(f"only someone in the group chat {room['title']!r} can bring others in")
        if bot not in self.crew.bots:
            raise UnknownBot(f"no bot named {bot!r}; the crew is {sorted(self.crew.bots)}")
        if bot in room["members"]:
            return room
        if len(room["members"]) >= MAX_ROOM_BOTS:
            raise NotAllowed(f"{room['title']!r} already has {MAX_ROOM_BOTS} bots, the most a group chat holds")
        room = self.store.update_room(room_id, members=[*room["members"], bot])
        self.bus.publish({"type": "room", "room": room})
        self.store.log_agent("add_to_group", task.bot, bot, room_id=room_id, text=room["title"])
        return room

    def post(self, room_id: str, author: str, text: str, depth: int = 0,
             attachments: list[dict[str, Any]] | None = None, *, recipients: list[str] | None = None) -> dict[str, Any]:
        """A message in a room, from the user or a bot. It wakes the bots it mentions; one from the user that mentions
        no one wakes the lead, who answers it, hands it on or leaves it. Once the bots have replied to each other the
        room's limit of times in a row, a bot's mentions wait for the user to continue."""
        room = self.store.room(room_id)
        named, wake = self._recipients(room, author, text) if recipients is None else (recipients, recipients)
        message = self.store.add_room_message(room_id, author, text, depth, attachments)
        self.bus.publish({"type": "room_message", "message": message})
        if author in self.crew.bots:
            self.store.log_agent("post", author, room_id=room_id, text=text)
        everyone = recipients is None and self._mentions(room, text)[1]
        for bot in (room["members"] if everyone else named):
            if bot != author and bot in self.crew.bots:
                self._room_mention(bot, room, message)
        if author == "user":
            if room["stopped"] or room["archived_at"]:  # saying something carries on, and brings it back
                room = self._set_room(room_id, stopped=None, archived_at=None)
        elif wake and room["max_depth"] is not None and depth >= room["max_depth"]:
            self._set_room(room_id, stopped={"after": depth, "waiting": wake})
            wake = []
        for bot in wake:
            self._wake_seat(room, bot, depth, owes_reply=author == "user" and bot in named)
        return message

    def carry_on(self, room_id: str) -> dict[str, Any]:
        """The user's "Continue" once the bots stopped: whoever was mentioned last is woken, with a fresh count."""
        room = self.store.room(room_id)
        if stopped := room["stopped"]:
            still_waiting = [bot for bot in stopped["waiting"] if (seat := self.store.seat_of(room_id, bot))
                             and seat.id in self._cancelling]
            remaining = {**stopped, "waiting": still_waiting} if still_waiting else None
            room = self._set_room(room_id, stopped=remaining)
            for bot in stopped["waiting"]:
                if bot in room["members"] and bot not in still_waiting:
                    self._wake_seat(room, bot, 0)
        return room

    def _room_mention(self, bot: str, room: dict[str, Any], message: dict[str, Any]) -> None:
        path = self.crew.memory.root / bot / "working-state" / "state.md"
        try:
            if working_state.room_mentions(path, room, [message], add=True):  # the seat answers the mention itself
                self._state_notice(bot, "State changed: 1 item needs you; older room mentions were trimmed.", wake=False)
        except Exception:
            logging.getLogger(__name__).exception("Could not record room mention for %s", bot)

    def _state_notice(self, bot: str, notice: str, warn: bool = False, wake: bool = True) -> None:
        """Tells the bot's main chat its state changed, quietly: a notice with no message text. A chat the user stopped
        isn't restarted by it: the notice waits, and goes with the user's next message, as one that needn't `wake` it
        does. A `warn` notice asks for attention, so the app always shows it; the routine ones can be hidden when the
        turn they start ends silent."""
        chat = self.chat(bot)
        self._record(chat.id, kind="state_notice", data={"message": notice, **({"warn": True} if warn else {})})
        if not wake or chat.id in self._cancelling or chat.status is Status.CANCELLED:
            self._held_notices.setdefault(chat.id, []).append(notice)
        else:
            self.send(chat.id, notice, sender="crewbots", _record_message=False)

    def need_added(self, task_id: str, trimmed: bool = False) -> None:
        """A session added a "Needs me" item itself: wake the bot's main chat, with no message text, as for a mention."""
        task = self.store.get_task(task_id)
        if self.chat(task.bot).id == task_id:
            return  # the writer is the main chat
        self._state_notice(task.bot, "State changed: 1 item needs you; older entries were trimmed." if trimmed
                           else "State changed: 1 item needs you.")

    def _clear_room_mentions(self, bot: str, room_id: str, through: int | None = None) -> None:
        try:
            working_state.room_mentions(self.crew.memory.root / bot / "working-state" / "state.md",
                                        {"id": room_id, "title": ""}, [], through=through)
        except Exception:
            logging.getLogger(__name__).exception("Could not clear room mentions for %s", bot)

    def set_room_limit(self, room_id: str, max_depth: int | None) -> dict[str, Any]:
        """How many bot replies in a row the room allows before the bots stop; None means no limit."""
        room = self._set_room(room_id, max_depth=max_depth)
        if max_depth is None and room["stopped"]:
            if room["archived_at"]:
                return self._set_room(room_id, stopped=None)
            return self.carry_on(room_id)
        return room

    def _set_room(self, room_id: str, **changes: Any) -> dict[str, Any]:
        room = self.store.update_room(room_id, **changes)
        self.bus.publish({"type": "room", "room": room})
        return room

    def _mentions(self, room: dict[str, Any], text: str) -> tuple[list[str], bool]:
        """The members a message mentions by name, in order, and whether it mentions @everyone."""
        members = {m.lower(): m for m in room["members"]}
        for m in room["members"]:  # a bot can also be mentioned by the name you gave it, written as one word
            if (bot := self.crew.bots.get(m)) and bot.display_name:
                members.setdefault(bot.display_name.replace(" ", "").lower(), m)
        said = [m.lower() for m in MENTION.findall(_mentionable(text))]
        return list(dict.fromkeys(members[m] for m in said if m in members)), EVERYONE in said

    def _recipients(self, room: dict[str, Any], author: str, text: str) -> tuple[list[str], list[str]]:
        named, everyone = self._mentions(room, text)
        if author == "user":
            return named, room["members"] if everyone else named or [room["lead"]]
        return named, [m for m in (room["members"] if everyone else named) if m != author]

    def _chief_room_item(self, room: dict[str, Any], author: str, kind: str, source: str) -> None:
        """Wake the main chat with metadata, keeping the room's conversation in the room."""
        from crewbots.memory import MemoryError
        from crewbots.working_state import edit

        chief = self.coordinator()
        if chief is None:
            return
        try:
            _, trimmed = edit(self.crew.memory.root / chief.name / "working-state" / "state.md", "Needs me", "add",
                              f"{room['title']} ({room['id']}): @{author} {kind} · {source}")
            notice = ("State changed: 1 item needs you; older entries were trimmed." if trimmed
                      else "State changed: 1 item needs you.")
        except (MemoryError, OSError):
            notice = f"State full or unavailable: @{author} needs you in room {room['id']} ({source}); tidy state."
            self._state_notice(chief.name, notice, warn=True)
            return
        self._state_notice(chief.name, notice)

    def post_from(self, task_id: str, text: str, room_id: str | None = None,
                  attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """The `post` tool: a bot says something in a room, from wherever it's working."""
        task = self.store.get_task(task_id)
        room = self._room_to_post(task, room_id)
        if task.kind == "seat" and task.room_id == room["id"]:
            self._clear_room_mentions(task.bot, room["id"], self._seat_turn_seen.get(task.id, 0))
        return self.post(room["id"], task.bot, text, depth=self._seat_depth.get(task_id, 0) + 1, attachments=attachments)

    def tell_user(self, task_id: str, text: str, needs_you: bool = False) -> dict[str, Any]:
        """The `tell_user` tool: the chief says something to the user in its chat with them from wherever it's working,
        a group chat or a thread, without waiting to be asked. Its chat hears it next time it runs."""
        task = self.store.get_task(task_id)
        if not self.is_chief(task.bot):
            raise NotAllowed("only the chief tells the user things directly: say it to whoever you work for")
        if not text.strip():
            raise NotAllowed("there's nothing to tell")
        where = " ".join(self.store.room(task.room_id)["title"].split()) if task.room_id else f"thread {task.id}"
        return self.attention.tell(task.bot, text.strip(), needs_you, where)

    def _room_to_post(self, task: Task, room_id: str | None) -> dict[str, Any]:
        """Where a bot's post goes: the room it names (by id, or by title among its own), else the one it's in, else
        the crew's; one it's a member of."""
        if room_id is None:
            room_id = task.room_id if task.kind == "seat" else self.crew_room()["id"]
        try:
            room = self.store.room(room_id)
        except KeyError:
            named = [r for r in self.store.rooms() if task.bot in r["members"]
                     and " ".join(r["title"].split()).lower() == " ".join(room_id.split()).lower()]
            if len(named) != 1:
                raise NotAllowed(f"no group chat {room_id}" if not named else
                                 f"you're in {len(named)} group chats called {room_id!r}: name one by its id") from None
            room = named[0]
        if task.bot not in room["members"]:
            raise NotAllowed(f"{task.bot} isn't in the group chat {room['title']!r}")
        if room["archived_at"]:
            raise NotAllowed(f"the group chat {room['title']!r} is archived")
        return room

    def _wake_seat(self, room: dict[str, Any], bot: str, depth: int, owes_reply: bool = False) -> None:
        if bot not in self.crew.bots:
            return  # a member since removed from the crew
        seat = self.store.seat_of(room["id"], bot) or self.store.create_task(
            bot, f"{room['title']} ({bot})", kind="seat", room_id=room["id"], status=Status.IDLE)
        if seat.id in self._cancelling:
            return
        key = f"seat:{seat.id}"
        previous = self.store.seen(key)
        unseen = [m for m in self.store.room_messages(room["id"], after=previous) if m["author"] != bot]
        if not unseen:
            return
        self.store.mark_seen(key, unseen[-1]["id"])
        # Woken again mid-turn, it keeps the deeper of the two, so a back-and-forth can't reset its own count.
        running = seat.id in self._runs
        self._seat_depth[seat.id] = max(depth, self._seat_depth.get(seat.id, 0)) if running else depth
        if owes_reply:
            self._owed.add(seat.id)
        try:
            self.send(seat.id, _catch_up([f"[{m['author']}]: {m['text']}{attachments_module.listing(m['attachments'])}"
                                          for m in unseen]), sender="room")
            if running:
                self._seat_queue_floor.setdefault(seat.id, previous)
        except NotAllowed:
            if owes_reply:
                self._owed.discard(seat.id)

    def _room_prompt_sent(self, task_id: str) -> None:
        seen = self._seat_dispatch.pop(task_id, None)
        floor = self._seat_queue_floor.get(task_id)
        if seen is not None and floor is not None and floor < seen:
            if self.store.seen(f"seat:{task_id}") > seen:
                self._seat_queue_floor[task_id] = seen
            else:
                self._seat_queue_floor.pop(task_id, None)

    def _queue_during_run(self, task: Task, text: str) -> Task:
        self._waiting.setdefault(task.id, []).append(text)
        # During seat maintenance the completed turn has no pending row. Its first new wake starts one.
        if task.kind == "seat" and task.status == Status.IDLE:
            with self.store.db.transaction():
                self.store.set_pending(task.id, text)
                self.store.update_task(task.id, status=Status.QUEUED)
            return self._set(task.id, status=Status.QUEUED)
        self.store.pending_waiting(task.id, text)
        return task

    def _seat_turn_finished(self, seat: Task, result: str) -> None:
        waiting = self._waiting.get(seat.id)
        result = "" if is_no_reply(result) else result
        status = Status.QUEUED if waiting else Status.IDLE
        with self.store.db.transaction():
            if waiting:
                self.store.set_pending(seat.id, "\n\n".join(waiting))
            else:
                self.store.clear_pending(seat.id)
            self.store.update_task(seat.id, status=status, result=result, turns=seat.turns + 1)
        self._set(seat.id, status=status, result=result, turns=seat.turns + 1)

    def _interrupt_after_shutdown(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        if task.kind != "seat" or task.status != Status.IDLE:
            self._set(task_id, status=Status.INTERRUPTED, error="the daemon stopped during this task")

    def _seat_replied(self, seat: Task, text: str, files: list[dict[str, Any]] | None = None,
                      owed: bool = False, nudged: bool = False) -> None:
        text = text.strip()
        self._clear_room_mentions(seat.bot, seat.room_id, self._seat_turn_seen.get(seat.id, 0))
        if not text or is_no_reply(text):
            if owed:
                self._nudged.add(seat.id)
                self.send(seat.id, prompts.text("mentioned", self.crew.home), sender="crewbots")
            elif nudged:
                self.post(seat.room_id, seat.bot, "I don't have an answer yet.",
                          depth=self._seat_depth.get(seat.id, 0) + 1)
            return
        self.post(seat.room_id, seat.bot, text, depth=self._seat_depth.get(seat.id, 0) + 1, attachments=files or None)


def _catch_up(said: list[str]) -> str:
    """What a bot missed in a group chat, newest last, within CATCH_UP: always the newest message, which woke it."""
    kept, size = [], 0
    for line in reversed(said):
        size += len(line) + 2
        if kept and size > CATCH_UP:
            break
        kept.append(line)
    left = len(said) - len(kept)
    return "\n\n".join(([f"({left} earlier messages you haven't seen are left out.)"] if left else [])
                       + kept[::-1])


def _mentionable(text: str) -> str:
    lines, kept, i = text.splitlines(), [], 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^\s*>", line):
            i += 1
            continue
        mark = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if mark:
            fence = mark[1]
            closing = re.compile(r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$")
            end = next((j for j in range(i + 1, len(lines)) if closing.match(lines[j])), None)
            if end is not None:
                i = end + 1
                continue
            i += 1  # An unclosed fence doesn't hide the lines after it.
            continue
        kept.append(line)
        i += 1
    return re.sub(r"(`+)(?!`)[^\n]*?(?<!`)\1(?!`)", "", "\n".join(kept))
