"""The commit message of a merge: the proposal's summary, as a subject line and a body."""

SUBJECT_LIMIT = 72


def check_summary(summary: str) -> None:
    """Refuses a summary whose first line, the commit's subject, is empty or too long to be one."""
    subject = summary.split("\n", 1)[0].strip()
    if not subject:
        raise ValueError("The summary's first line is the commit's subject, and it is empty: start the summary with "
                         f"one line of at most {SUBJECT_LIMIT} characters that says what the change does")
    if len(subject) > SUBJECT_LIMIT:
        raise ValueError(f"The summary's first line is the commit's subject, and it is {len(subject)} characters, "
                         f"over the limit of {SUBJECT_LIMIT}: rewrite it as a shorter line, and put the detail after "
                         "a blank line")


def merge_message(summary: str, title: str, *, unreviewed: bool = False) -> str:
    """The summary's first line as the subject, a blank line, then the rest of the summary. Work merged with no
    usable summary (the user merged it without a proposal) gets its title as the subject when that fits, else
    "Merge work" with the title in the body, and any summary it has in the body.
    Work merged without approval ends with a `Merged-without-review: yes` trailer."""
    try:
        check_summary(summary)
        subject, _, body = summary.partition("\n")
        subject = subject.strip()
    except ValueError:
        title = " ".join(title.split())
        if title and len(title) <= SUBJECT_LIMIT:
            subject, body = title, summary.strip()
        else:
            subject, body = "Merge work", "\n\n".join(part for part in (summary.strip(), title) if part)
    body = body.strip()
    if unreviewed:
        body = f"{body}\n\nMerged-without-review: yes" if body else "Merged-without-review: yes"
    return f"{subject}\n\n{body}" if body else subject
