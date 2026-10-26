"""No source file grows too long to read comfortably. The limits are about readability, not targets: a file past one
is split by area, or named here with the reason it isn't."""

from pathlib import Path

ROOT = Path(__file__).parent.parent
LIMITS = {".py": 700, ".tsx": 450, ".ts": 450, ".css": 900}
FOLDERS = [ROOT / "src", ROOT / "app" / "src"]
ALLOWED = {  # path from the repository's root: why it may be longer
    "src/crewbots/reviews.py": "code review is frozen: split it by area when it's next worked on",
    "app/src/api-types.ts": "generated from the API's schema (crewbots openapi --ts)",
}


def lines(path: Path) -> int:
    return path.read_text().count("\n")


def test_every_source_file_is_within_its_limit():
    over = []
    for folder in FOLDERS:
        for path in folder.rglob("*"):
            limit = LIMITS.get(path.suffix)
            name = str(path.relative_to(ROOT))
            if limit and "node_modules" not in path.parts and name not in ALLOWED and lines(path) > limit:
                over.append(f"{name}: {lines(path)} lines (limit {limit})")
    assert not over, "split these by area, or add them to ALLOWED with a reason:\n" + "\n".join(over)


def test_the_allowed_files_still_need_it():
    # Once a file is back under its limit, it comes off the list, so the list stays honest.
    for name in ALLOWED:
        path = ROOT / name
        assert path.exists(), f"{name} is gone: take it off ALLOWED"
        assert lines(path) > LIMITS[path.suffix], f"{name} is within its limit now: take it off ALLOWED"
