"""The vendored official documents match the hashes recorded for them (FR-RR-019)."""

import hashlib
from pathlib import Path

import yaml

SOURCES_DIR = Path(__file__).resolve().parents[4] / "report-defs" / "sources"


def _sources() -> dict[str, dict[str, str]]:
    with (SOURCES_DIR / "SOURCES.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)["sources"]


def test_every_available_source_is_committed_with_its_hash():
    for key, source in _sources().items():
        assert source["url"].startswith("https://"), key
        if source["status"] == "UNAVAILABLE":
            assert "file" not in source, key
            continue
        data = (SOURCES_DIR / source["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == source["sha256"], key


def test_the_2027_schema_is_recorded_as_unavailable():
    assert _sources()["AVI2_XSD"]["status"] == "UNAVAILABLE"
