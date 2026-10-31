"""The user's attention: what the crew raises on its own (a check-in's news, or what a bot tells the user from a group
chat or a thread) reaches them at one of three levels.

- **FYI** waits for the morning digest, which the chief sends at DIGEST_HOUR: what came up in the last day.
- **Question** goes to its bot's chat at a natural break: when none of the user's work is running and they haven't
  written for a few minutes, outside quiet hours.
- **Needs you** comes at once, with a notification, outside quiet hours and within a budget of so many a day across
  the crew; past that it waits as a question.

The user's 👍 or 👎 on one becomes a note of that bot's, about what's worth raising.
"""

import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from crewbots import prompts
from crewbots.memory import MemoryError

if TYPE_CHECKING:
    from crewbots.runner import Runner

BY_URGENCY = {"low": "fyi", "normal": "question", "high": "needs_you"}
DAILY_INTERRUPTIONS = 3  # "needs you" at once, across the crew, a day
QUIET_HOURS = (22, 8)  # no interruptions from 22:00 to 08:00
DIGEST_HOUR = 8
BREAK_S = 300.0  # the user wrote this recently: they're mid-conversation, and a question waits
EXPIRES = timedelta(hours=24)  # an FYI older than this is left out of the digest
TOLD = "told from "  # the source of what a bot told the user from elsewhere, before where it was


class Attention:
    """Raises what the crew finds to the user, at the right level and time."""

    def __init__(self, runner: "Runner"):
        self.runner = runner
        self.user_wrote = 0.0  # time.time() of the user's last message
        self.now = lambda: datetime.now().astimezone()  # the clock (tests set their own)

    def raise_(self, bot: str, level: str, text: str, source: str) -> dict[str, Any]:
        """Something a bot found, on its own: delivered now, at the next break, or in the morning's digest."""
        store = self.runner.store
        now = self.now()
        if level == "needs_you" and (self.quiet(now) or self.interruptions_today(now) >= DAILY_INTERRUPTIONS):
            level = "question"  # it waits for a good moment instead
        item = store.add_proactive(bot, level, text, source, (now + EXPIRES).astimezone(UTC).isoformat())
        if level == "needs_you":
            self._deliver(item)
        elif level == "question" and self.at_a_break(now):
            self._deliver(item)
        return store.proactive_item(item["id"])

    def tell(self, bot: str, text: str, needs_you: bool, where: str) -> dict[str, Any]:
        """What a bot tells the user in its chat with them from somewhere else (a group chat, a thread), whenever it
        has something for them: shown there at once, and with a notification when it needs them, outside quiet hours
        and within the day's budget."""
        store = self.runner.store
        now = self.now()
        urgent = needs_you and not self.quiet(now) and self.interruptions_today(now) < DAILY_INTERRUPTIONS
        item = store.add_proactive(bot, "needs_you" if urgent else "question", text, f"{TOLD}{where}",
                                   (now + EXPIRES).astimezone(UTC).isoformat())
        self.runner._record(self.runner.chat(bot).id, kind="text", data={"text": text, "level": item["level"],
                                                                         "from": where})
        store.mark_delivered(item["id"])
        return store.proactive_item(item["id"])

    def tick(self, now: datetime) -> None:
        """Delivers waiting questions at a natural break, and the morning digest once it's due."""
        store = self.runner.store
        if self.at_a_break(now):
            for item in store.proactive_waiting("question"):
                self._deliver(item)
        if now.hour >= DIGEST_HOUR and not self.quiet(now) and not store.digested_today(_midnight(now)):
            if self.digest(now) is None and self.runner.chief_bot() is not None:
                store.mark_digested(_midnight(now), [])  # an empty day: what comes up later waits for tomorrow's

    def digest(self, now: datetime) -> dict[str, Any] | None:
        """The morning digest: the last day's FYIs, in the chief's chat. With no FYIs, or no chief, nothing is sent."""
        store = self.runner.store
        items = [i for i in store.proactive_waiting("fyi") if i["expires_at"] > now.astimezone(UTC).isoformat()]
        if not items:
            return None
        crew = self.runner.crew
        chief = self.runner.chief_bot()
        if chief is None:
            return None
        store.mark_digested(_midnight(now), [i["id"] for i in items])
        lines = [f"- {crew.bots[i['bot']].label if i['bot'] in crew.bots else i['bot']}: {i['text']}" for i in items]
        text = prompts.text("digest", crew.home, items="\n".join(lines))
        chat = self.runner.chat(chief.name)
        self.runner._record(chat.id, kind="text", data={"text": text, "digest": True, "items": [i["id"] for i in items]})
        return {"bot": chief.name, "items": len(items)}

    def react(self, item_id: int, reaction: str) -> dict[str, Any]:
        """The user's 👍 or 👎 on something raised: kept, and a note of its bot's, about what's worth raising."""
        store = self.runner.store
        item = store.react_proactive(item_id, reaction)
        worth = "Worth raising" if reaction == "up" else "Not worth raising"
        gist = " ".join(item["text"].split())[:80]
        try:
            self.runner.crew.memory.remember(item["bot"], f"{worth}: {gist}", f"The user gave this a "
                                             f"{'thumbs up' if reaction == 'up' else 'thumbs down'} when you raised it "
                                             f"({item['level'].replace('_', ' ')}): {item['text']}", "the user's reaction")
        except MemoryError:
            pass
        return store.proactive_item(item_id)

    def quiet(self, now: datetime) -> bool:
        start, end = QUIET_HOURS
        return now.hour >= start or now.hour < end

    def at_a_break(self, now: datetime) -> bool:
        return not self.quiet(now) and not self.runner.user_busy() and time.time() - self.user_wrote >= BREAK_S

    def interruptions_today(self, now: datetime) -> int:
        return self.runner.store.delivered_since("needs_you", _midnight(now))

    def _deliver(self, item: dict[str, Any]) -> None:
        if item["bot"] not in self.runner.crew.bots:
            return
        chat = self.runner.chat(item["bot"])
        self.runner._record(chat.id, kind="text", data={"text": item["text"], "checkin": True, "level": item["level"],
                                                        "proactive_id": item["id"]})
        self.runner.store.mark_delivered(item["id"])


def _midnight(now: datetime) -> str:
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC).isoformat()
