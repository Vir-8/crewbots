"""Threads and delegation: a bot opening a thread, handing a piece of work to a teammate, and results going back up
when the work is done.

Any bot can delegate; only coordinators add bots and propose work for merging. What keeps delegation from running
away is checked here, in code: no delegating to yourself, nor back to a bot already in the chain (a circle), a chain at
most a few delegations deep, a cap on how many tasks one bot has out at once, and the budgets."""

from typing import Any

from crewbots import cards, prompts
from crewbots import crew as crew_module
from crewbots.backends import Access
from crewbots.crew import Bot
from crewbots.events import Notice
from crewbots.runner.common import NotAllowed, UnknownBot, _clip
from crewbots.runner.reviews import MAX_REVIEW_ROUNDS, _review_findings, _round_status
from crewbots.store import Handoff, Review, Status, Task

MAX_CHILDREN = 12  # a stop condition: a task that keeps delegating is looping, not managing
MAX_DEPTH = 3  # delegations in a row: chief → engineer → helper → one more, and no further
MAX_OUT = 4  # tasks one bot has delegated that aren't finished yet, across all its work


class Delegation:
    """The part of Runner that opens threads, hands out work, and reports results back up."""

    def start_thread(self, task_id: str, title: str, goal: str) -> Task:
        """A bot opens a thread for itself, under the conversation it's in. Its result comes back there."""
        parent = self.store.get_task(task_id)
        _not_answering(parent)
        if len(self.store.children(task_id)) >= self.max_children:
            raise NotAllowed(f"stop condition: this conversation already opened {self.max_children} threads. Finish or "
                             "report on those before opening more.")
        return self.submit(parent.bot, goal, created_by=parent.bot, parent_id=task_id, workspace=parent.workspace,
                           title=title)

    def delegate(self, parent_id: str, bot: str, goal: str, *, workspace: str | None = None,
                 review_of: str | None = None) -> Task:
        parent = self.store.get_task(parent_id)
        _not_answering(parent)
        if bot not in self.crew.bots:
            raise UnknownBot(f"no bot named {bot!r}; the crew is {sorted(self.crew.bots)}")
        if bot == parent.bot:
            raise NotAllowed("that's you: to work on it yourself, open a thread with start_thread")
        if self.crew.bots[bot].can_delegate:
            raise NotAllowed(f"{bot} is a coordinator; delegate to the bot that will do the work")
        chain = self._chain(parent)
        if bot in {t.bot for t in chain}:
            way = " → ".join(dict.fromkeys(t.bot for t in reversed(chain)))
            raise NotAllowed(f"{bot} is already in this chain of work ({way}); handing it back would go round in a "
                             "circle. Hand back with `handoff`, or ask with `escalate`, instead.")
        depth = 1 + sum(1 for t in chain if _delegated(t))
        if depth > self.max_depth:
            raise NotAllowed(f"stop condition: that would be {depth} delegations deep, and the most is "
                             f"{self.max_depth}. Do it yourself, or hand back what you have and what's left.")
        out = self.store.delegated_out(parent.bot)
        if out >= self.max_out:
            raise NotAllowed(f"stop condition: you already have {out} delegated tasks that haven't finished, and the "
                             f"most is {self.max_out}. Wait for their results, which come to you, before delegating "
                             "more.")
        if reason := self.hold_reason(parent) or self.budget_reason(bot, parent):
            raise NotAllowed(f"no budget for more work: {reason}")
        if len(self.store.children(parent_id)) >= self.max_children:
            raise NotAllowed(
                f"stop condition: this task already delegated {self.max_children} tasks. Report to the user "
                "with what you have and what is still open, instead of delegating more.")
        version = None
        if review_of:
            work = self._own_child(parent_id, review_of)
            if work.active or work.id in self._runs:
                raise NotAllowed(f"{work.id} is still working; ask for a review once it has finished")
            if work.review_of:
                raise NotAllowed(f"{work.id} is itself a review; review the work it reviewed instead")
            state = self.review_state(work.id)
            version = work.turns
            if version not in state.rounds and state.round >= MAX_REVIEW_ROUNDS:
                raise NotAllowed(
                    f"stop condition: {work.id} has had {MAX_REVIEW_ROUNDS} review rounds. Report to the user with the "
                    "best version and the open objections, instead of another round (team/rules.md).")
            workspace = work.worktree_path or work.workspace
        return self.submit(bot, goal, created_by=parent.bot, parent_id=parent_id,
                           workspace=workspace or parent.workspace, review_of=review_of, review_version=version)

    def ask(self, task_id: str, bot: str, question: str) -> Task:
        """A bot asks a teammate a quick question. The teammate answers in a small task of its own (kind `ask`),
        read-only and with no worktree, and starts nothing from it; the answer comes back like a thread's result."""
        parent = self.store.get_task(task_id)
        _not_answering(parent)
        if bot not in self.crew.bots:
            raise UnknownBot(f"no bot named {bot!r}; the crew is {sorted(self.crew.bots)}")
        if bot == parent.bot:
            raise NotAllowed("that's you: answer it yourself")
        out = self.store.delegated_out(parent.bot)
        if out >= self.max_out:
            raise NotAllowed(f"stop condition: you already have {out} questions and delegated tasks that haven't "
                             f"finished, and the most is {self.max_out}. Wait for their answers, which come to you.")
        if reason := self.hold_reason(parent) or self.budget_reason(bot, parent):
            raise NotAllowed(f"no budget for a question: {reason}")
        return self.submit(bot, prompts.text("ask", self.crew.home, asker=parent.bot, question=question),
                           created_by=parent.bot, parent_id=task_id, title=" ".join(question.split()), kind="ask")

    def children_of(self, parent_id: str) -> list[dict[str, Any]]:
        return [self._summary(c, include_result=True) for c in self.store.children(parent_id)]

    def message_child(self, parent_id: str, task_id: str, text: str) -> Task:
        child = self._own_child(parent_id, task_id)
        if child.status is not Status.NEEDS_INPUT and self.review_state(task_id).out_of_rounds:
            raise NotAllowed(
                f"stop condition: {task_id} failed review in all {MAX_REVIEW_ROUNDS} rounds. Report to the user with "
                "the best version and the open objections, instead of sending it back again (team/rules.md).")
        return self.send(task_id, text, sender=self.store.get_task(parent_id).bot)

    def add_bot(self, task_id: str, fields: dict[str, Any]) -> Bot:
        """A coordinator adds a bot, when the user asks it to, with whatever it gives it: either access short of full
        (no sandbox is the user's call), any of the crew's MCP servers (all of them unless it names some), delegation,
        and network unless the coordinator has none itself. Nothing lets a bot remove or change one: that's the user's."""
        maker = self.bot_for(self.store.get_task(task_id).bot)
        if maker is None or not maker.can_delegate:
            raise NotAllowed("only a bot that coordinates the crew can add bots")
        access = Access(fields.get("access") or Access.READ_ONLY)
        if access is Access.FULL:
            raise NotAllowed("full access, with no sandbox, is the user's to give, in the app")
        network = fields.get("network", maker.network)
        if network and not maker.network:
            raise NotAllowed("you have no network, so a bot you add can't either; the user can give it some")
        mcp = list(fields["mcp"]) if fields.get("mcp") is not None else sorted(self.crew.mcp_servers)
        name = str(fields["name"]).strip().lower()
        card = {"display_name": fields.get("display_name"), "title": fields["title"],
                "description": fields["description"], "backend": fields.get("backend") or maker.backend,
                "model": fields.get("model"), "effort": fields.get("effort"), "access": access.value,
                "network": list(network) if isinstance(network, tuple) else network, "mcp": mcp,
                "skills": list(fields.get("skills") or []), "can_delegate": bool(fields.get("can_delegate")) or None}
        path = cards.save_card(self.crew.home, name, card, str(fields["instructions"]), create=True)
        try:
            crew = crew_module.load(self.crew.home)
        except crew_module.CrewError:
            path.unlink(missing_ok=True)  # the crew must still load without it
            raise
        self.reload(crew)
        added = crew.bots[name]
        self._record(task_id, Notice(message=f"Added {added.label} (@{added.name}), {added.title}, to the crew."))
        return added

    def _report_up(self, task_id: str) -> None:
        """Once none of a parent's children are running, sends it everything they finished since its last report."""
        task = self.store.get_task(task_id)
        if not task.parent_id or task.active or task_id in self._runs:
            return
        parent = self.store.get_task(task.parent_id)
        if parent.status is Status.CANCELLED:
            return
        siblings = self.store.children(task.parent_id)
        if any(s.active or s.id in self._runs for s in siblings):
            return
        finished = [s for s in siblings if not s.reported]
        if not finished:
            return
        for s in finished:
            self.store.update_task(s.id, reported=True)
        if parent.kind == "review":  # a review's reviewers: their findings, whole, for the lead to put together
            self.send(parent.id, sender="crewbots", text=_review_findings(parent, finished))
            return
        sections = "\n\n".join(
            _report_section(s, self.store.latest_handoff(s.id), self.store.latest_review(s.id)) for s in finished)
        reviewed = dict.fromkeys(s.review_of for s in finished if s.review_of)
        status = "\n".join(_round_status(self.review_state(w), self.store.get_task(w)) for w in reviewed)
        asked = sum(1 for s in finished if s.kind == "ask")
        head = (f"{len(finished)} thread(s) you opened or delegated finished, and none are still running."
                if asked < len(finished) else "Your question was answered." if asked == 1
                else f"Your {asked} questions were answered.")
        self.send(task.parent_id, sender="crewbots", text=(
            f"{head}\n\n{sections}" + (f"\n\n# Review status\n\n{status}" if status else "")))

    def _chain(self, task: Task) -> list[Task]:
        """The task and every task above it, up to the one that started the work."""
        chain = [task]
        while chain[-1].parent_id:
            chain.append(self.store.get_task(chain[-1].parent_id))
        return chain

    def _own_child(self, parent_id: str, task_id: str) -> Task:
        try:
            child = self.store.get_task(task_id)
        except KeyError:
            raise NotAllowed(f"no task {task_id}") from None
        if child.parent_id != parent_id:
            raise NotAllowed(f"task {task_id} was not delegated by this task")
        return child

    def _summary(self, task: Task, include_result: bool = False) -> dict[str, Any]:
        summary: dict[str, Any] = {"id": task.id, "bot": task.bot, "title": task.title, "status": task.status}
        if task.branch:
            summary |= {"worktree": task.worktree_path, "branch": task.branch}
        if include_result:
            handoff = self.store.latest_handoff(task.id)
            summary |= {"handoff": handoff.to_json() if handoff else None, "result": _clip(task.result),
                        "error": task.error}
        return summary


def _report_section(task: Task, handoff: Handoff | None, review: Review | None = None) -> str:
    if task.kind == "ask":
        answer = _clip(task.result) if task.status is Status.IDLE and task.result else f"(no answer: {task.status})"
        return f"## {task.bot} answered ({task.id})\nYou asked: {task.title}\n{answer}"
    lines = [f"## {task.id} · {task.bot} · {task.status}", f"Task: {task.title}"]
    if task.review_of:
        lines.append(f"Review of {task.review_of} (version {task.review_version}): "
                     + (review.verdict.upper() if review else "NO VERDICT RECORDED"))
        if review:
            lines.append(f"Summary: {review.summary}")
            lines += [f"- {f.get('location', '?')}: {f.get('problem', '')}"
                      + (f" Scenario: {f['scenario']}" if f.get("scenario") else "") for f in review.findings]
    if task.branch:
        lines.append(f"Worktree: {task.worktree_path} (branch {task.branch})")
    if task.error:
        lines.append(f"Error: {task.error}")
    if handoff:
        lines += ["HANDOFF", f"Done: {handoff.done}", f"Output: {', '.join(handoff.output) or '(none)'}",
                  f"Open: {handoff.open}", f"Needs: {handoff.needs}"]
    if task.result:
        lines += ["Final message:", _clip(task.result) or ""]
    return "\n".join(lines)


def _delegated(task: Task) -> bool:
    """Whether a bot handed this task to another (rather than the user starting it, or a bot opening it for itself)."""
    return bool(task.parent_id) and task.created_by not in ("user", task.bot) and not task.created_by.startswith(
        "routine")


def _not_answering(task: Task) -> None:
    if task.kind == "ask":
        raise NotAllowed("you're answering a teammate's question: answer it, rather than starting more work")
