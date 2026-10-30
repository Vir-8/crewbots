"""Splitting a Markdown file into its YAML front matter and body. Each caller loads the YAML and reports problems in
its own way: a role card refuses what a memory note lets by."""

import re

FENCE = re.compile(r"^---[ \t]*\r?$", re.MULTILINE)


def split(text: str) -> tuple[str, str] | None:
    """The YAML between a first line of `---` and the next line of `---`, and the body after it. None when the file
    has no front matter or it's never closed. A `---` inside a value or the body doesn't end it."""
    if not (opening := FENCE.match(text)) or not (closing := FENCE.search(text, opening.end())):
        return None
    return text[opening.end():closing.start()], text[closing.end():]
