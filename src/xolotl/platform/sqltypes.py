"""Column types shared by every module."""

from enum import StrEnum

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
