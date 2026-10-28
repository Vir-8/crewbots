"""The commit message of a merge: the proposal's summary, as a subject line and a body."""

import re


def _merge_subject(summary: str, title: str, limit: int = 72) -> str:
    return _subject(summary, title, limit)[0]


def _subject(summary: str, title: str, limit: int) -> tuple[str, bool]:
    """The subject, and whether it was taken from the summary (not made from the title)."""
    paragraph = " ".join(re.split(r"\n\s*\n", summary.strip(), maxsplit=1)[0].split())
    abbreviation = r"\b(?:e\.g|i\.e|etc|mr|mrs|ms|dr|vs|inc)\.$"
    if paragraph and len(paragraph) <= limit and not re.fullmatch(abbreviation, paragraph, re.I):
        return paragraph, True
    for end in re.finditer(r"[.!?](?=\s|$)", paragraph):
        sentence = paragraph[:end.end()]
        if len(sentence) > limit:
            break
        if len(sentence) >= 20 and not re.search(abbreviation, sentence, re.I):
            return sentence, True
    fallback = " ".join(title.split()) or "Merge work"
    if len(fallback) <= limit:
        return fallback, False
    prefix = fallback[:limit - 1]
    if not prefix[-1].isspace() and not fallback[limit - 1].isspace() and " " in prefix:
        prefix = prefix.rsplit(" ", 1)[0]
    return prefix.rstrip() + "…", False


def merge_message(summary: str, title: str, *, unreviewed: bool = False) -> str:
    """The subject, a blank line, then the rest of the summary (all of it if the subject came from the title). Work
    merged without approval ends with a `Merged-without-review: yes` trailer."""
    subject, from_summary = _subject(summary, title, 72)
    text = summary.strip()
    opening = from_summary and re.match(r"\s*".join(map(re.escape, subject.split())), text)
    body = (text[opening.end():] if opening else text).strip()
    if unreviewed:
        body = f"{body}\n\nMerged-without-review: yes" if body else "Merged-without-review: yes"
    return f"{subject}\n\n{body}" if body else subject
