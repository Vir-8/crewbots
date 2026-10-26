"""Code review: pull requests, their drafts and reviews, and who reviews them."""

from typing import Literal

from pydantic import BaseModel

from crewbots.api.schemas.tasks import TaskOut


class DraftOut(BaseModel):
    """A line comment for a review, private until the user submits it."""

    id: int
    path: str
    line: int
    side: Literal["RIGHT", "LEFT"]
    body: str
    author: str  # "user", or the reviewer that drafted it
    task_id: str | None
    commit_sha: str | None  # the head it was written against
    created_at: str
    updated_at: str

class DraftBody(BaseModel):
    path: str
    line: int
    side: Literal["RIGHT", "LEFT"] = "RIGHT"
    body: str

class DraftEdit(BaseModel):
    body: str

class SubmitReview(BaseModel):
    event: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]
    body: str = ""
    drafts: list[int] | None = None  # which drafts go with it; None: all of them

class Submitted(BaseModel):
    url: str
    state: str  # GitHub's: APPROVED, CHANGES_REQUESTED or COMMENTED
    comments: int

class ImageOut(BaseModel):
    mime: str
    data: str  # base64

class PullSummary(BaseModel):
    repo: str
    number: int
    title: str
    url: str
    draft: bool
    author: str
    author_avatar: str | None
    created_at: str | None
    updated_at: str | None
    additions: int
    deletions: int
    changed_files: int
    review_decision: str | None
    checks: str | None  # SUCCESS, FAILURE, PENDING, ... for the head commit
    reviewed_at: str | None = None  # when a review of yours in crewbots was last touched

class Inbox(BaseModel):
    """Pull requests waiting on you, which never include your own, and your own, apart. Only the lists asked for are
    there."""

    assigned: list[PullSummary] | None = None  # assigned to you
    direct: list[PullSummary] | None = None  # asking you for a review
    team: list[PullSummary] | None = None  # asking one of your teams
    mine: list[PullSummary] | None = None  # yours
    error: str | None  # GitHub's, for a list it couldn't read; that list is the last good one, or empty

class PullFile(BaseModel):
    path: str
    additions: int
    deletions: int

class Checks(BaseModel):
    total: int
    passed: int
    failed: int

class PullOut(BaseModel):
    repo: str
    number: int
    title: str
    body: str
    url: str
    state: str | None
    draft: bool
    author: str
    head: str
    base: str
    head_sha: str
    base_sha: str = ""
    additions: int
    deletions: int
    changed_files: int
    review_decision: str | None
    created_at: str | None
    updated_at: str | None
    labels: list[str]
    files: list[PullFile]
    checks: Checks
    failing: list[str]  # the checks that fail
    issues: list[int]  # the issues it refers to
    people: list[str] = []  # who a comment might @mention: the author first
    checkout: str | None  # the local copy reviews read, if the repo is mapped
    reviews: list[TaskOut]

class ReviewRequest(BaseModel):
    note: str = ""  # anything to look at in particular

class Reviewer(BaseModel):
    backend: Literal["claude", "codex"]
    model: str | None = None  # None: the CLI's own default
    effort: str | None = None

class RepoFolder(BaseModel):
    folder: str | None
