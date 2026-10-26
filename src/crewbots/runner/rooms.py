"""Group chats: posting, waking the bots a message mentions (or the lead), and posting a bot's reply back, a few replies
deep at most."""

import re
from typing import Any

from crewbots import attachments as attachments_module
from crewbots.crew import NO_REPLY
from crewbots.runner.common import NotAllowed, UnknownBot
from crewbots.store import Status, Task

MAX_ROOM_DEPTH = 4  # bot replies in a row in a room, after the user's message, before the bots stop waking each other
CREW_ROOM = "Crew"  # the group chat every bot is in, made when the crew first needs one
MENTION = re.compile(r"@([\w-]+)")
MAX_ROOM_BOTS = 6  # bots in one group chat: more and they talk past each other


class Rooms:
    """The part of Runner that runs group chats."""

    def crew_room(self) -> dict[str, Any]:
        """The group chat with every bot in it, led by the coordinator."""
        for room in self.store.rooms():
            if room["title"] == CREW_ROOM:
                return room
        bots = list(self.crew.bots.values())
        lead = next((b.name for b in bots if b.can_delegate), bots[0].name if bots else "")
        return self.create_room(CREW_ROOM, [b.name for b in bots], lead)

    def create_room(self, title: str, members: list[str], lead: str | None = None) -> dict[str, Any]:
        unknown = [m for m in members if m not in self.crew.bots]
        if unknown or not members:
            raise UnknownBot(f"no bots named {unknown}; the crew is {sorted(self.crew.bots)}" if unknown
                             else "a group chat needs at least one bot")
        lead = lead or members[0]
        if lead not in members:
            raise UnknownBot(f"the lead, {lead}, isn't in the group chat")
        room = self.store.create_room(title.strip() or ", ".join(members), lead, members)
        self.bus.publish({"type": "room", "room": room})
        return room

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
        return self.create_room(title, members, lead or task.bot)

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
        room = self.store.set_room_members(room_id, [*room["members"], bot])
        self.bus.publish({"type": "room", "room": room})
        return room

    def post(self, room_id: str, author: str, text: str, depth: int = 0,
             attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """A message in a room, from the user or a bot. It wakes the bots it mentions; one from the user that mentions
        no one wakes the lead."""
        room = self.store.room(room_id)
        message = self.store.add_room_message(room_id, author, text, depth, attachments)
        self.bus.publish({"type": "room_message", "message": message})
        members = {m.lower(): m for m in room["members"]}
        for m in room["members"]:  # a bot can also be mentioned by the name you gave it, written as one word
            if (bot := self.crew.bots.get(m)) and bot.display_name:
                members.setdefault(bot.display_name.replace(" ", "").lower(), m)
        mentioned = list(dict.fromkeys(members[m.lower()] for m in MENTION.findall(text) if m.lower() in members))
        if author == "user":
            wake = mentioned or [room["lead"]]
        else:
            wake = [m for m in mentioned if m != author] if depth < MAX_ROOM_DEPTH else []
        for bot in wake:
            self._wake_seat(room, bot, depth)
        return message

    def post_from(self, task_id: str, text: str, room_id: str | None = None) -> dict[str, Any]:
        """The `post` tool: a bot says something in a room, from wherever it's working."""
        task = self.store.get_task(task_id)
        if room_id is None:
            room_id = task.room_id if task.kind == "seat" else self.crew_room()["id"]
        try:
            room = self.store.room(room_id)
        except KeyError:
            raise NotAllowed(f"no group chat {room_id}") from None
        if task.bot not in room["members"]:
            raise NotAllowed(f"{task.bot} isn't in the group chat {room['title']!r}")
        return self.post(room_id, task.bot, text, depth=self._seat_depth.get(task_id, 0) + 1)

    def _wake_seat(self, room: dict[str, Any], bot: str, depth: int) -> None:
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
        self.send(seat.id, "\n\n".join(f"[{m['author']}]: {m['text']}{attachments_module.listing(m['attachments'])}"
                                       for m in unseen), sender="room")

    def _seat_replied(self, seat: Task, text: str) -> None:
        text = text.strip()
        if not text or text.strip("`*_. ") == NO_REPLY:
            return
        self.post(seat.room_id, seat.bot, text, depth=self._seat_depth.get(seat.id, 0) + 1)
