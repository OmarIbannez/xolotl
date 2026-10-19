"""Column types, and the rule for the JSON values they store, shared by every module."""

from enum import StrEnum
from typing import cast

from sqlalchemy import Enum as SAEnum


def enum_column[E: StrEnum](enum_cls: type[E]) -> SAEnum:
    """UPPERCASE enum values in a VARCHAR(32), never a native Postgres enum (D20).

    Stores the member value, not the member name, and skips native enums so
    adding a member never needs an ``ALTER TYPE`` migration. Alembic compares
    it as ``VARCHAR(32)``, which is what migration 0001 declares.

    Returns:
        A non-native ``Enum`` column type for ``enum_cls`` backed by VARCHAR(32).

    """

    def values(members: type[E]) -> list[str]:
        return [member.value for member in members]

    return SAEnum(enum_cls, native_enum=False, length=32, values_callable=values)


def contains_float(value: object) -> bool:
    """Whether a JSON-shaped value holds a float at any depth.

    Values that must come back from a JSONB column exactly as they went in (the
    audit chain recomputes its digest from the stored row, an event is rebuilt from
    its outbox row) cannot hold floats: JSONB stores numbers as ``numeric``, which
    does not round-trip every float. ``1e16`` comes back as the integer
    ``10000000000000000`` and ``-0.0`` comes back as ``0.0``. Use ints or strings
    instead, such as an amount in minor units or a decimal string.

    Walks dicts and lists with an explicit stack, so a deeply nested value cannot
    exhaust the call stack.

    Returns:
        ``True`` when ``value``, or anything nested in a dict or list, is a float.

    """
    pending: list[object] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, float):
            return True
        if isinstance(item, dict):
            pending.extend(cast("dict[object, object]", item).values())
        elif isinstance(item, list):
            pending.extend(cast("list[object]", item))
    return False
