"""Code review: the pull requests waiting on you, their drafts, reviews and submitting them."""

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException

from crewbots.api.context import Context
from crewbots.api.schemas import (
    ChangesOut,
    DraftBody,
    DraftEdit,
    DraftOut,
    ImageOut,
    Inbox,
    PullOut,
    RepoFolder,
    Reviewer,
    ReviewRequest,
    SubmitReview,
    Submitted,
    TaskOut,
)
from crewbots.github import GitHubError
from crewbots.reviews import INBOX_LISTS, SEARCHES, ReviewError, reviewer_name


def routes(c: Context) -> APIRouter:
    runner, store, reviews = c.runner, c.store, c.reviews
    starting = c.starting
    v1 = APIRouter()

    def reviewing(fn, *args):
        try:
            return fn(*args)
        except (GitHubError, ReviewError) as e:
            raise HTTPException(502 if isinstance(e, GitHubError) else 422, str(e)) from None

    @v1.get("/reviews/inbox", response_model=Inbox)
    async def review_inbox(lists: str = ",".join(INBOX_LISTS), fresh: bool = False):
        """`lists`: any of assigned, direct, team and mine. Kept answers come back at once; `fresh` waits for GitHub."""
        asked = tuple(k for k in lists.split(",") if k in SEARCHES) or INBOX_LISTS
        inbox, error = await asyncio.to_thread(reviews.inbox, asked, fresh)
        touched = store.reviewed_prs()
        return {k: [p | {"reviewed_at": touched.get(f"{p['repo']}#{p['number']}")} for p in v] for k, v in inbox.items()} | {
            "error": error}

    @v1.get("/reviews/{owner}/{name}/{number}/drafts", response_model=list[DraftOut])
    async def review_drafts(owner: str, name: str, number: int):
        return store.drafts(f"{owner}/{name}", number)

    @v1.post("/reviews/{owner}/{name}/{number}/drafts", response_model=DraftOut, status_code=201)
    async def add_review_draft(owner: str, name: str, number: int, body: DraftBody):
        repo = f"{owner}/{name}"
        spot = await asyncio.to_thread(reviewing, reviews.check_draft, repo, number, body.path, body.line, body.side)
        return store.add_draft(repo, number, spot["path"], spot["line"], spot["side"], body.body, "user",
                               commit_sha=spot["commit_sha"])

    @v1.put("/reviews/drafts/{draft_id}", response_model=DraftOut)
    async def edit_review_draft(draft_id: int, body: DraftEdit):
        try:
            return store.edit_draft(draft_id, body.body)
        except KeyError:
            raise HTTPException(404, "that draft is gone") from None

    @v1.delete("/reviews/drafts/{draft_id}")
    async def delete_review_draft(draft_id: int):
        store.delete_drafts([draft_id])
        return {"deleted": draft_id}

    @v1.post("/reviews/{owner}/{name}/{number}/submit", response_model=Submitted)
    async def submit_review(owner: str, name: str, number: int, body: SubmitReview):
        """Posts the user's review to GitHub, approving, requesting changes or commenting, with the drafts chosen."""
        repo = f"{owner}/{name}"
        drafts = [d for d in store.drafts(repo, number) if body.drafts is None or d["id"] in body.drafts]
        done = await asyncio.to_thread(reviewing, reviews.submit, repo, number, body.event, body.body, drafts)
        store.delete_drafts([d["id"] for d in drafts])
        return done

    @v1.get("/reviews/{owner}/{name}/{number}/image", response_model=ImageOut)
    async def review_image(owner: str, name: str, number: int, path: str, side: Literal["head", "base"] = "head"):
        """An image the pull request adds or changes, for the diff to show."""
        return await asyncio.to_thread(reviewing, reviews.image, f"{owner}/{name}", number, path, side)

    @v1.get("/reviews/{owner}/{name}/{number}", response_model=PullOut)
    async def review_pull(owner: str, name: str, number: int):
        repo = f"{owner}/{name}"
        pull = await asyncio.to_thread(reviewing, reviews.pull, repo, number)
        return pull | {"reviews": [t.to_json() for t in store.reviews_of(f"{repo}#{number}")]}

    @v1.get("/reviews/{owner}/{name}/{number}/diff", response_model=ChangesOut)
    async def review_diff(owner: str, name: str, number: int):
        return {"diff": await asyncio.to_thread(reviewing, reviews.diff, f"{owner}/{name}", number)}

    @v1.post("/reviews/{owner}/{name}/{number}/review", status_code=201, response_model=list[TaskOut])
    async def review_start(owner: str, name: str, number: int, body: ReviewRequest | None = None):
        """A private review: each of your reviewer models reviews the pull request on its own, then the first, as the
        lead, puts their findings together into one review, with its line comments drafted. Returns the lead, whose
        conversation it is; the reviewers start once the code is checked out, which the first review of a
        repository takes about a minute for."""
        repo, note = f"{owner}/{name}", body.note if body else ""
        pr = await asyncio.to_thread(reviewing, reviews.pull, repo, number)
        names = [reviewer_name(r) for r in reviews.reviewers()]
        lead = runner.start_review(names[0], title=f"Review {repo}#{number}: {pr['title']}", pr=f"{repo}#{number}",
                                   request=reviews.request(repo, number, note))

        async def begin() -> None:
            runner.note(lead.id, f"Getting {repo}'s code. The first review of a repository takes about a minute; "
                                 "later ones, a few seconds.")
            try:
                folder, whole = await asyncio.to_thread(reviews.checkout, repo, number)
                goal = await asyncio.to_thread(reviews.goal, repo, number, note, whole)
            except (GitHubError, ReviewError) as e:
                runner.fail(lead.id, f"couldn't get the pull request's code: {e}")
                return
            runner.start_reviewers(lead.id, names, goal, str(folder))

        starting.add(task := asyncio.create_task(begin()))
        task.add_done_callback(starting.discard)
        return [lead.to_json()]

    @v1.get("/reviews/reviewers", response_model=list[Reviewer])
    async def get_reviewers():
        return reviews.reviewers()

    @v1.put("/reviews/reviewers", response_model=list[Reviewer])
    async def put_reviewers(body: list[Reviewer]):
        """The models that review a pull request, each with its reasoning effort."""
        return reviewing(reviews.set_reviewers, [r.model_dump() for r in body])

    @v1.get("/reviews/repos", response_model=dict[str, str])
    async def review_repos():
        return reviews.repos()

    @v1.put("/reviews/repos/{owner}/{name}", response_model=dict[str, str])
    async def review_repo(owner: str, name: str, body: RepoFolder):
        """Maps a repo to your local checkout of it, so reviews read the code, not just the diff."""
        return reviewing(reviews.set_repo, f"{owner}/{name}", body.folder)

    return v1
