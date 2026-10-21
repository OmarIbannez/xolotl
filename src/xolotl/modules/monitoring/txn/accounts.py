"""A relationship's accounts, created on first sight (SP-04 spec §4, §6.1 step 1).

An item without ``account_ref`` books to the relationship's ``DEFAULT`` account,
keyed ``default:<relationship_id>``. An ``account_ref`` is the client's own key,
unique per tenant: the first item that names it binds it to its relationship, and
naming it under another relationship later is a conflict.
"""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.enums import AccountKind
from xolotl.modules.monitoring.txn.models import Account
from xolotl.modules.party.service import RelationshipRef
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7


class AccountRefConflictError(Exception):
    """An ``account_ref`` is already bound to another relationship."""


def _default_ref(relationship_id: UUID) -> str:
    return f"default:{relationship_id}"


def account_key(relationship_id: UUID, account_ref: str | None) -> str:
    """Return the ``external_ref`` an item's account is kept under.

    Returns:
        ``account_ref``, or the relationship's ``DEFAULT`` key when it is None.

    """
    return _default_ref(relationship_id) if account_ref is None else account_ref


def bound_relationships(session: Session, keys: Collection[str]) -> dict[str, UUID]:
    """Read which relationship each existing account key is bound to.

    Returns:
        The relationship id by ``external_ref``, for the keys that exist.

    """
    rows = session.execute(
        select(Account.external_ref, Account.relationship_id).where(
            Account.external_ref.in_(keys)
        )
    ).all()
    return {row[0]: row[1] for row in rows}


def _upsert(
    session: Session,
    relationship: RelationshipRef,
    external_ref: str,
    kind: AccountKind,
) -> Account:
    tenant_id = bound_context(session).require_tenant_id()
    statement = (
        insert(Account)
        .values(
            tenant_id=tenant_id,
            id=uuid7(),
            relationship_id=relationship.id,
            party_id=relationship.party_id,
            external_ref=external_ref,
            kind=kind,
        )
        .on_conflict_do_update(
            index_elements=[Account.tenant_id, Account.external_ref],
            set_={"last_seen_at": text("now()")},
        )
        .returning(Account.id)
    )
    account_id = session.execute(statement).scalar_one()
    return session.get_one(Account, (tenant_id, account_id), populate_existing=True)


def default_account(session: Session, relationship: RelationshipRef) -> Account:
    """Return the relationship's ``DEFAULT`` account, creating it once.

    Returns:
        The account.

    """
    return _upsert(
        session, relationship, _default_ref(relationship.id), AccountKind.DEFAULT
    )


def account_for(
    session: Session, relationship: RelationshipRef, account_ref: str | None
) -> Account:
    """Resolve an item's account, creating it on first sight.

    Returns:
        The ``DEFAULT`` account when ``account_ref`` is None, else the account with
        that reference (kind ``OTHER`` when new), its ``last_seen_at`` moved on.

    Raises:
        AccountRefConflictError: When the reference belongs to another
            relationship.

    """
    if account_ref is None:
        return default_account(session, relationship)
    account = _upsert(session, relationship, account_ref, AccountKind.OTHER)
    if account.relationship_id != relationship.id:
        msg = "account_ref is bound to another relationship"
        raise AccountRefConflictError(msg)
    return account


def find_account(
    session: Session, relationship_id: UUID, account_ref: str | None
) -> Account | None:
    """Read an item's account without creating one (the decide path).

    Returns:
        The account with ``account_ref``, or the relationship's ``DEFAULT``
        account when it is None; None when there is no such row.

    """
    return session.scalar(
        select(Account).where(
            Account.external_ref == account_key(relationship_id, account_ref)
        )
    )
