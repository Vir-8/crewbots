"""Comments that excuse or defer, in the lines a piece of work adds: a workaround explained rather than fixed, a TODO
for what's unfinished, review feedback written into the code. An agent leaves one to justify a shortcut, and the next
copies it as if it were a rule (pstack's no-comments, and Lauren Tan's Dune, which bans comments outright), so code
work isn't handed back with one: the cause is fixed, or what's open goes in the handoff. A line that links an issue,
a constraint from outside the project, may stay."""

import re
from pathlib import Path

CODE = {".cs", ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs", ".go", ".java", ".kt", ".swift", ".c", ".cc",
        ".cpp", ".h", ".hpp", ".m", ".mm", ".rb", ".sh", ".zsh", ".lua", ".gd", ".shader", ".hlsl", ".cginc", ".compute",
        ".glsl", ".css", ".scss", ".sql", ".uss"}
COMMENT = re.compile(r"//|#|/\*|^\s*\*|--|<!--")
EXCUSE = re.compile(r"\b(TODO|FIXME|HACK|XXX|work-?around|work around|temporar(y|ily)|for now|band-?aid|quick fix|"
                    r"as requested|per review|reviewer|do not remove|don't remove)\b", re.IGNORECASE)
SHOWN = 8  # lines named in a refusal


def excuses(diff: str) -> list[str]:
    """The lines a unified diff adds to code with an excuse in a comment, as `file:line: text`."""
    found: list[str] = []
    file, line = None, 0
    for row in diff.splitlines():
        if row.startswith("+++ "):
            file = row[6:] if row.startswith("+++ b/") else None
        elif row.startswith("@@"):
            start = re.search(r"\+(\d+)", row)
            line = int(start[1]) if start else 0
        elif file and row.startswith("+"):
            text = row[1:]
            comment = COMMENT.search(text)
            if (Path(file).suffix.lower() in CODE and comment and EXCUSE.search(text[comment.start():])
                    and "http" not in text):
                found.append(f"{file}:{line}: {text.strip()[:120]}")
            line += 1
        elif file and not row.startswith("-"):
            line += 1
    return found


def added(path: Path, name: str) -> str:
    """A new file, as the diff that adds it."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return f"+++ b/{name}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)


def refusal(found: list[str]) -> str:
    listed = "\n".join(found[:SHOWN]) + (f"\n… and {len(found) - SHOWN} more" if len(found) > SHOWN else "")
    return ("your change adds comments that excuse or defer, which the crew's standards don't allow, since the next bot "
            f"copies them as rules:\n{listed}\nFix the cause, or remove the note and put what's unfinished in `open`. A "
            "constraint from outside the project (an engine or vendor quirk) can stay, said as a fact with its issue link.")
