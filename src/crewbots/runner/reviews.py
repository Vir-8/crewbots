"""The review round: reviews of a piece of work counted in versions, and code review's lead and reviewers."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from crewbots import worktree
from crewbots.runner.checks import Checks
from crewbots.runner.common import NotAllowed
from crewbots.store import Review, Status, Task, Verdict

MAX_REVIEW_ROUNDS = 3  # a stop condition: after this many reviewed versions, the user decides
REVIEW_FINDINGS_CHARS = 20_000  # of each reviewer's findings, for the lead


@dataclass(frozen=True)
class ReviewState:
    """Where a piece of work stands in review. A round is one reviewed version of the work (its `turns` when the
    reviews were asked for), so a review of an older version never approves newer changes: unless the work's change is
    still the one reviewed, after a turn that changed nothing or a clean rebase (`same_change`)."""

    work_id: str
    version: int  # the work as it is now
    rounds: list[int]  # the versions that were reviewed, in order
    latest: list[tuple[Task, Review | None]]  # the review tasks of the last round, with their verdicts
    same_change: bool = False  # it's on a later version than the last round's, with the same change

    @property
    def round(self) -> int:
        return len(self.rounds)

    @property
    def settled(self) -> bool:
        return bool(self.latest) and all(not t.active and r is not None for t, r in self.latest)

    @property
    def approved(self) -> bool:
        """Every review of the current version is in, and every one passed."""
        return (self.settled and (self.rounds[-1] == self.version or self.same_change)
                and all(r.verdict is Verdict.PASS for _, r in self.latest))

    @property
    def failed(self) -> bool:
        return self.settled and any(r.verdict is Verdict.FAIL for _, r in self.latest)

    @property
    def out_of_rounds(self) -> bool:
        return self.round >= MAX_REVIEW_ROUNDS and self.failed

    def to_json(self) -> dict[str, Any]:
        return {"work_id": self.work_id, "version": self.version, "round": self.round, "max_rounds": MAX_REVIEW_ROUNDS,
                "approved": self.approved, "failed": self.failed, "out_of_rounds": self.out_of_rounds,
                "reviews": [{"task_id": t.id, "reviewer": t.bot, "status": t.status,
                             "verdict": r.verdict if r else None, "summary": r.summary if r else None,
                             "findings": r.findings if r else []} for t, r in self.latest]}


class Reviewing(Checks):
    """The part of Runner that counts review rounds, and runs code review's lead and reviewers."""

    def start_review(self, lead: str, *, title: str, pr: str, request: str) -> Task:
        """A private review of a pull request: the lead's task, which waits while its reviewers review the pull
        request on their own (`start_reviewers`, once their code is ready), then puts their findings together."""
        task = self.store.create_task(lead, request, created_by="user", title=title, pr=pr, kind="review",
                                      status=Status.QUEUED)
        self.bus.publish({"type": "task", "task": task.to_json()})
        self._message(task.id, "user", request)
        return task

    def start_reviewers(self, lead_id: str, reviewers: list[str], goal: str, folder: str) -> list[Task]:
        """The reviewers of a review, each on its own, in the same checkout. When they've all finished, their findings
        go to the lead (`_report_up`)."""
        if self.store.get_task(lead_id).status is not Status.QUEUED:
            return []  # stopped while its code was being fetched
        lead = self.store.update_task(lead_id, workspace=folder)
        started = []
        for reviewer in reviewers:
            task = self.store.create_task(reviewer, goal, created_by=lead.bot, parent_id=lead_id, workspace=folder,
                                          title=lead.title, pr=lead.pr, kind="review")
            self.bus.publish({"type": "task", "task": task.to_json()})
            self._message(task.id, "user", goal)
            self._start(task.id, goal)
            started.append(task)
        return started

    def review(self, task_id: str, verdict: Verdict, summary: str, findings: list[dict[str, str]]) -> Review:
        task = self.store.get_task(task_id)
        if not task.review_of:
            raise NotAllowed("only a review task gives a verdict; this task is not a review")
        review = self.store.add_review(task_id, task.review_of, task.bot, task.review_version or 0, verdict, summary,
                                       findings)
        self._record(task_id, kind="review", data=review.to_json())
        self._record(task.review_of, kind="review", data=review.to_json())
        self._report_late(task_id)  # a verdict given after the review task's turn ended still reaches its lead
        return review

    def review_state(self, work_id: str) -> ReviewState:
        work = self.store.get_task(work_id)
        reviews = self.store.reviewers_of(work_id)
        rounds = sorted({r.review_version for r in reviews if r.review_version is not None})
        latest = [(r, self.store.latest_review(r.id)) for r in reviews if rounds and r.review_version == rounds[-1]]
        state = ReviewState(work_id=work_id, version=work.turns, rounds=rounds, latest=latest)
        if state.settled and rounds[-1] != work.turns and all(r.verdict is Verdict.PASS for _, r in latest):
            reviewed = {e.data.get("change") if (e := self.store.last_event(t.id, ("review_change",))) else None
                        for t, _ in latest}
            if len(reviewed) == 1 and (change := reviewed.pop()) and change == self._change_of(work):
                state = replace(state, same_change=True)
        return state

    def _change_of(self, work: Task) -> str:
        """The id of what `work` changes now, against where it merges (worktree.change_id); "" when that can't be
        told, so only the same version counts."""
        if not (work.repo and work.worktree_path and Path(work.worktree_path).exists()):
            return ""
        try:
            onto = work.base_branch or worktree.git_read(Path(work.repo), "rev-parse", "HEAD")
            return worktree.change_id(Path(work.worktree_path), onto)
        except (worktree.GitError, OSError):
            return ""

    def _review_brief(self, task: Task) -> str | None:
        if not task.review_of:
            return None
        work = self.store.get_task(task.review_of)
        rounds = self.review_state(work.id).rounds
        n = rounds.index(task.review_version) + 1 if task.review_version in rounds else len(rounds)
        where = ("A throwaway copy of its work is your working directory, and you may write there: run the checks "
                 "and the change itself (the project's verify skill, if it has one, says how). Nothing you change "
                 "there reaches the work, so report what you find rather than fixing it." if self._in_review_copy(task)
                 else "Its work is in your working directory.")
        return (f"This task is review round {n} of {MAX_REVIEW_ROUNDS} of task {work.id} ({work.bot}: {work.title}). "
                f"{where} The uncommitted changes (`git diff`, and new files) are what you review, against the "
                "handback's evidence. Finish by calling `review(verdict, summary, findings)`: verdict `pass` or "
                "`fail`, one finding per problem, each with its location, the problem, a failure scenario, and proof "
                "(the command you ran and what it showed). A pass with remaining risks is still a pass; list them as "
                "findings.")


def _round_status(state: ReviewState, work: Task) -> str:
    head = f"{work.id} ({work.bot}): round {state.round} of {MAX_REVIEW_ROUNDS}"
    if not state.settled:
        return f"{head}: some reviews of this round gave no verdict; ask them with message, or review again."
    if state.approved:
        return (f"{head}: approved; every review passed. Propose it to the user with propose_merge(task_id="
                f"{work.id!r}, summary=...).")
    if state.out_of_rounds:
        return (f"{head}: STOP CONDITION. It failed review in every round. Do not send it back again: report to the "
                "user with the best version and the open objections.")
    if state.failed:
        return (f"{head}: changes requested. Send the findings you judge real to the owner with message(task_id="
                f"{work.id!r}, ...); its next version needs a new review round ({state.round + 1} of "
                f"{MAX_REVIEW_ROUNDS}).")
    return f"{head}: reviewed an older version ({state.rounds[-1]}); the work has changed since, so review it again."


def _review_findings(lead: Task, reviewers: list[Task]) -> str:
    """The lead's first message: what each reviewer found, whole, or that it failed."""
    def who(task: Task) -> str:
        _, backend, model, effort = (task.bot.split(":") + ["", "", ""])[:4]
        return f"{backend} {'(its default model)' if model == 'default' else model}, {effort} effort"

    parts = []
    for r in reviewers:
        if r.status is Status.FAILED:
            parts.append(f"## {who(r)}\n\nThis reviewer failed ({r.error}): review what it would have covered yourself.")
        else:
            found = r.result or "(it found nothing to report)"
            if len(found) > REVIEW_FINDINGS_CHARS:
                found = found[:REVIEW_FINDINGS_CHARS] + "\n…"
            parts.append(f"## {who(r)}\n\n{found}")
    return (f"The reviewers have finished: {lead.title}. Their findings follow.\n\n" + "\n\n".join(parts)
            + "\n\nCheck them against the code, put them together, draft the line comments, and reply with the review.")
