"""Group chats: posting, waking the bots a message mentions (or the lead, or @everyone), and posting a bot's reply back,
a few replies deep at most. A bot @mentioned by name owes the room a reply."""

import re
from datetime import UTC, datetime
from typing import Any

from crewbots import attachments as attachments_module
from crewbots import prompts
from crewbots.crew import NO_REPLY
from crewbots.runner.common import NotAllowed, UnknownBot, _iso
from crewbots.store import Status, Task

MAX_ROOM_DEPTH = 4  # bot replies in a row in a room, after the user's message, before the bots stop waking each other
CREW_ROOM = "Crew"  # the group chat every bot is in, made when the crew first needs one
MENTION = re.compile(r"@([\w-]+)")
EVERYONE = "everyone"  # @everyone wakes every bot in the room
MAX_ROOM_BOTS = 6  # bots in one group chat: more and they talk past each other


class Rooms:
    """The part of Runner that runs group chats."""

    def crew_room(self) -> dict[str, Any]:
        """The group chat with every bot in it, led by the coordinator."""
        for room in self.store.rooms():
            if room["crew"]:
                return room
        bots = list(self.crew.bots.values())
        lead = next((b.name for b in bots if b.can_delegate), bots[0].name if bots else "")
        return self.create_room(CREW_ROOM, [b.name for b in bots], lead, crew=True)

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
        from their next turn. Archived, it's kept but out of the way, and its bots aren't woken there."""
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
        if changes.keys() & {"title", "lead", "members"}:
            for seat in self.store.seats_in(room_id):
                if "title" in changes:
                    self._set(seat.id, title=f"{room['title']} ({seat.bot})")
                if seat.id not in self._runs:
                    await self._close_session(seat.id)  # its next turn starts from the room as it is now
        return room

    async def delete_room(self, room_id: str) -> None:
        """Deletes a group chat and what was said in it. Work it handed out carries on, and reports to each bot's own
        chat instead."""
        room = self.store.room(room_id)
        if room["crew"]:
            raise NotAllowed("the crew's own group chat can't be deleted")
        for seat in self.store.seats_in(room_id):
            if seat.active or seat.id in self._runs:
                await self.cancel(seat.id)
            await self._close_session(seat.id)
            self.store.clear_pending(seat.id)
            self._owed.discard(seat.id)
            self._seat_depth.pop(seat.id, None)
            for child in self.store.children(seat.id):
                self._set(child.id, parent_id=self.chat(seat.bot).id)
        self.store.delete_room(room_id)
        self.bus.publish({"type": "room_gone", "room_id": room_id})

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
             attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """A message in a room, from the user or a bot. It wakes the bots it mentions; one from the user that mentions
        no one wakes the lead, who answers it, hands it on or leaves it. Once the bots have replied to each other the
        room's limit of times in a row, a bot's mention wakes no one: the room says they stopped, until the user carries
        on (`carry_on`) or says something new."""
        room = self.store.room(room_id)
        message = self.store.add_room_message(room_id, author, text, depth, attachments)
        self.bus.publish({"type": "room_message", "message": message})
        if author in self.crew.bots:
            self.store.log_agent("post", author, room_id=room_id, text=text)
        named, everyone = self._mentions(room, text)
        if author == "user":
            if room["stopped"] or room["archived_at"]:  # saying something carries on, and brings it back
                room = self._set_room(room_id, stopped=None, archived_at=None)
            wake = room["members"] if everyone else named or [room["lead"]]
        else:
            wake = [m for m in (room["members"] if everyone else named) if m != author]
            if wake and depth >= (room["max_depth"] or MAX_ROOM_DEPTH):
                self._set_room(room_id, stopped={"after": depth, "waiting": wake, "owed": [m for m in wake if m in named]})
                wake = []
        for bot in wake:
            self._wake_seat(room, bot, depth, owes_reply=bot in named)
        return message

    def carry_on(self, room_id: str) -> dict[str, Any]:
        """The user's "Continue" once the bots stopped: whoever was mentioned last is woken, with a fresh count."""
        room = self.store.room(room_id)
        if stopped := room["stopped"]:
            room = self._set_room(room_id, stopped=None)
            for bot in stopped["waiting"]:
                if bot in room["members"]:
                    self._wake_seat(room, bot, 0, owes_reply=bot in stopped["owed"])
        return room

    def set_room_limit(self, room_id: str, max_depth: int | None) -> dict[str, Any]:
        """How many bot replies in a row the room allows before the bots stop; None for the crew's default."""
        self.store.room(room_id)
        return self._set_room(room_id, max_depth=max_depth)

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
        said = [m.lower() for m in MENTION.findall(text)]
        return list(dict.fromkeys(members[m] for m in said if m in members)), EVERYONE in said

    def post_from(self, task_id: str, text: str, room_id: str | None = None,
                  attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """The `post` tool: a bot says something in a room, from wherever it's working."""
        task = self.store.get_task(task_id)
        room = self._room_to_post(task, room_id)
        return self.post(room["id"], task.bot, text, depth=self._seat_depth.get(task_id, 0) + 1, attachments=attachments)

    def _room_to_post(self, task: Task, room_id: str | None) -> dict[str, Any]:
        """Where a bot's post goes: the room it names, else the one it's in, else the crew's; one it's a member of."""
        if room_id is None:
            room_id = task.room_id if task.kind == "seat" else self.crew_room()["id"]
        try:
            room = self.store.room(room_id)
        except KeyError:
            raise NotAllowed(f"no group chat {room_id}") from None
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
        key = f"seat:{seat.id}"
        unseen = [m for m in self.store.room_messages(room["id"], after=self.store.seen(key)) if m["author"] != bot]
        if not unseen:
            return
        self.store.mark_seen(key, unseen[-1]["id"])
        # Woken again mid-turn, it keeps the deeper of the two, so a back-and-forth can't reset its own count.
        running = seat.id in self._runs
        self._seat_depth[seat.id] = max(depth, self._seat_depth.get(seat.id, 0)) if running else depth
        if owes_reply:
            self._owed.add(seat.id)
        self.send(seat.id, "\n\n".join(f"[{m['author']}]: {m['text']}{attachments_module.listing(m['attachments'])}"
                                       for m in unseen), sender="room")

    def _seat_replied(self, seat: Task, text: str, files: list[dict[str, Any]] | None = None) -> None:
        text = text.strip()
        owed = seat.id in self._owed
        self._owed.discard(seat.id)
        if not text or text.strip("`*_. ") == NO_REPLY:
            if owed:  # asked by name, it says something: once, so a bot that really has nothing can still pass
                self.send(seat.id, prompts.text("mentioned", self.crew.home, no_reply=NO_REPLY), sender="crewbots")
            return
        self.post(seat.room_id, seat.bot, text, depth=self._seat_depth.get(seat.id, 0) + 1, attachments=files or None)
