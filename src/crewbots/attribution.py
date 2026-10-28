"""Keeps AI attribution out of the commits crewbots makes: Vir never approved a "Co-Authored-By: Claude" trailer.

Every commit the daemon creates (squash merge, branch-mode merge, the save of uncommitted work, the memory repo) takes
its message through `final_message`, which removes the AI trailers and then refuses a message that still has one. The
commits are made with hooks off, so the message checked is the message that lands.

A trailer is removed only when it is clearly an AI: one of the known AI noreply addresses, or a GitHub `[bot]` of an
AI. A name on its own never counts, so a person who is named Claude, or who works at openai.com, and bots such as
dependabot are kept.
"""

import re
import subprocess
from pathlib import Path

from crewbots import paths

AI_ADDRESSES = {"noreply@anthropic.com", "noreply@openai.com", "codex@openai.com"}
AI_BOT = re.compile(r"claude|codex|copilot|openai|chatgpt|gpt|gemini", re.I)
TRAILER = re.compile(r"^co-authored-by[ \t]*:(?P<who>.*)$", re.I)
# The footer line the tools write with the trailer.
GENERATED = re.compile(r"^(?:🤖[ \t]*)?generated (?:with|by)\b.*\b(?:claude code|claude|codex)\b.*$", re.I)


class AttributionError(ValueError):
    pass


def is_ai(who: str) -> bool:
    """Whether the value of a Co-authored-by trailer ("Name <email>") names an AI."""
    who = " ".join(who.split())
    match = re.fullmatch(r"(?P<name>.*?)\s*(?:<(?P<email>[^>]*)>)?", who)
    name, email = match["name"].strip(), (match["email"] or "").strip()
    github_bot = "[bot]" in name.lower() or "[bot]" in email.lower()
    return email.lower() in AI_ADDRESSES or (github_bot and bool(AI_BOT.search(name + " " + email)))


def _entries(message: str) -> list[tuple[list[str], bool]]:
    """The message as (lines, is_ai) entries: a trailer with its folded continuation lines is one entry."""
    entries: list[tuple[list[str], bool]] = []
    for line in message.splitlines():
        if line[:1] in (" ", "\t") and entries and entries[-1][0] and TRAILER.match(entries[-1][0][0]):
            lines = [*entries[-1][0], line]
            entries[-1] = (lines, is_ai(TRAILER.match(lines[0])["who"] + " " + " ".join(lines[1:])))
            continue
        trailer = TRAILER.match(line)
        entries.append(([line], bool(trailer and is_ai(trailer["who"])) or bool(GENERATED.match(line.strip()))))
    return entries


def without_ai(message: str) -> str:
    """The message without its AI co-author trailers (folded ones too) and "Generated with" lines; the rest, human
    trailers included, is kept as it was."""
    lines = [line for lines, ai in _entries(message) if not ai for line in lines]
    text = "\n".join(lines).rstrip()
    return text + "\n" if message.endswith("\n") and text else text


def check(message: str) -> None:
    for lines, ai in _entries(message):
        if ai:
            raise AttributionError(f"commit message carries AI attribution ({' '.join(lines[0].split())}); "
                                   "refusing to commit it")


def final_message(message: str) -> str:
    cleaned = without_ai(message)
    check(cleaned)
    return cleaned


def canonical(message: str) -> str:
    """`final_message`, then through `git stripspace`: the text Git itself would store (no trailing spaces, no runs of
    blank lines, one final newline). It is committed as it is and compared as it is."""
    text = final_message(message)
    done = subprocess.run([paths.git(), "stripspace"], input=text, capture_output=True, text=True)
    if done.returncode:
        raise AttributionError(f"git stripspace failed: {done.stderr.strip()}")
    return done.stdout


def verify_stored(repo: Path, commit: str, message: str) -> None:
    """AttributionError unless `commit` carries exactly `message`; nothing is undone."""
    done = subprocess.run([paths.git(), "-C", str(repo), "cat-file", "commit", commit], capture_output=True, text=True)
    stored = done.stdout.partition("\n\n")[2] if done.returncode == 0 else None
    if stored != message:
        raise AttributionError(f"commit {commit[:12]} was stored with a message other than the one checked")
