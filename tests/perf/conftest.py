"""Seeded synthetic data and the measurement record of the perf tests (F7).

Every name is drawn from the library's frequency tables by the library's seeded
``Draw(SEED)`` (not ``random``, which ruff's S311 refuses):
given names from the hard-negative corpus (the most frequent Mexican given names)
and surnames from ``data/surnames_mx.csv``. List entries go through the library's
perturbation generator with a ``Draw(SEED)``, so a run reproduces its own data.

``record`` appends one measurement to ``.perf/sp03.json`` (a JSON array, gitignored),
or to the file it is given (SP-04's tests pass ``SP04_FILE``), with the matcher
version, the commit and the time it was taken.
"""

import csv
import json
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from xolotl.modules.screening.matching import MATCHER_VERSION
from xolotl.modules.screening.matching.evaluation import Draw
from xolotl.modules.screening.matching.idf import surname_shares

ROOT = Path(__file__).resolve().parents[2]
PERF_FILE = ROOT / ".perf" / "sp03.json"
SP04_FILE = ROOT / ".perf" / "sp04.json"
HARD_NEGATIVES = (
    ROOT / "tests" / "modules" / "screening" / "corpus" / "hard_negatives.csv"
)
SEED = 20261019

type Measure = int | float | str
type Name = tuple[str, str, str]


def _git_dir() -> Path:
    dot_git = ROOT / ".git"
    if dot_git.is_file():
        # A worktree: ".git" names its own git directory.
        return Path(dot_git.read_text().removeprefix("gitdir:").strip())
    return dot_git


def _head() -> str:
    """Resolve ``HEAD`` by reading the git directory, without running git.

    Returns:
        The commit hash, or ``unknown`` outside a checkout.

    """
    git_dir = _git_dir()
    head_file = git_dir / "HEAD"
    if not head_file.exists():
        return "unknown"
    head = head_file.read_text().strip()
    if not head.startswith("ref: "):
        return head
    ref = head.removeprefix("ref: ")
    common = git_dir / "commondir"
    shared = (git_dir / common.read_text().strip()) if common.exists() else git_dir
    for base in (git_dir, shared):
        if (base / ref).exists():
            return (base / ref).read_text().strip()
    packed = shared / "packed-refs"
    for line in packed.read_text().splitlines() if packed.exists() else []:
        if line.endswith(f" {ref}"):
            return line.split()[0]
    return "unknown"


def record(name: str, file: Path = PERF_FILE, /, **measures: Measure) -> None:
    """Append one measurement to ``file``, ``.perf/sp03.json`` by default.

    ``file`` is positional, so a splatted measure never lands in it.
    """
    file.parent.mkdir(exist_ok=True)
    rows: list[object] = (
        json.loads(file.read_text(encoding="utf-8")) if file.exists() else []
    )
    rows.append({
        "name": name,
        "measures": measures,
        "matcher_version": MATCHER_VERSION,
        "git_head": _head(),
        "recorded_at": datetime.now(UTC).isoformat(),
    })
    _ = file.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


@cache
def given_names() -> tuple[str, ...]:
    """Read the distinct given names of the hard-negative persons, sorted.

    Returns:
        About sixty frequent Mexican given names.

    """
    with HARD_NEGATIVES.open(encoding="utf-8") as f:
        rows = csv.DictReader(line for line in f if not line.startswith("#"))
        return tuple(
            sorted({r["name"].split()[0] for r in rows if r["entity_type"] == "PERSON"})
        )


@cache
def surnames() -> tuple[str, ...]:
    """Read the surnames of the library's frequency table, most frequent first.

    Returns:
        A hundred folded Mexican surnames, capitalised.

    """
    return tuple(name.capitalize() for name in surname_shares())


def split_pools() -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
    """Split the tables in two disjoint pools: the party base's and the lists'.

    Returns:
        ``((party_given, party_surnames), (list_given, list_surnames))``.

    """
    given, family = given_names(), surnames()
    return ((given[0::2], family[0::2]), (given[1::2], family[1::2]))


def draw_name(rng: Draw, given: tuple[str, ...], family: tuple[str, ...]) -> Name:
    """Draw a person's name: one or two given names and two distinct surnames.

    Returns:
        ``(given_names, first_surname, second_surname)``.

    """
    count = 2 if rng.below(10) < 3 else 1
    first, second = rng.sample(family, 2)
    return (" ".join(rng.sample(given, count)), first, second)
