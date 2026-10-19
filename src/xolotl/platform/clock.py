"""Single source of wall-clock time so tests can freeze it with monkeypatch."""

from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now(UTC)
