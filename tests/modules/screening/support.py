"""Fixture paths and the golden-file assertion for the screening library tests.

A golden is one canonical JSON entry per line. ``XOLOTL_UPDATE_GOLDENS=1`` rewrites
the file instead of comparing; review the diff before committing it.
"""

import json
import os
from pathlib import Path

from xolotl.modules.screening.lists import ParsedList

FIXTURES = Path(__file__).parent / "fixtures"
GOLDENS = Path(__file__).parent / "lists" / "goldens"


def golden_lines(parsed: ParsedList) -> list[str]:
    header = {
        "list_code": parsed.list_code,
        "parser_version": parsed.parser_version,
        "published_at": parsed.published_at and parsed.published_at.isoformat(),
        "source_version": parsed.source_version,
    }
    return [json.dumps(header, sort_keys=True, ensure_ascii=False)] + [
        e.canonical_json().decode() for e in parsed.entries
    ]


def assert_golden(name: str, parsed: ParsedList) -> None:
    path = GOLDENS / f"{name}.jsonl"
    lines = golden_lines(parsed)
    if os.environ.get("XOLOTL_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    expected = path.read_text(encoding="utf-8").splitlines()
    # Comparing lists makes pytest print the differing entries, not the whole file.
    assert lines == expected
