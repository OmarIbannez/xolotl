"""The retention sweep: purge the parties whose retention period has ended.

A party is due when it is ``ACTIVE``, its ``retention_until`` lies before today, it
is not on legal hold and none of its customer relationships is still open. A purge
nulls the encrypted names, deletes the party's identifiers, contacts, addresses,
ownership edges and documents, marks it ``PURGED``, audits ``party.purged`` and
publishes ``party.purged.v1``; its customer relationships stay (ids, statuses and
references only). The documents' objects are deleted after the purge commits, so a
rollback never loses a file; a delete that fails leaves an orphan object, logged.

The sweep is the ``party.retention_sweep`` job, scheduled daily per tenant, and
``xolotl party purge-due`` runs it by hand. Purging twice purges nothing.
"""

import logging
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import ColumnElement, delete, exists, func, select
from sqlalchemy.orm import Session

from xolotl.modules.party.customer import OPEN_STATUSES
from xolotl.modules.party.documents import document_ref
from xolotl.modules.party.enums import PartyStatus
from xolotl.modules.party.events import PARTY_PURGED, publish_party_event
from xolotl.modules.party.models import (
    CustomerRelationship,
    Document,
    Organization,
    Party,
    PartyAddress,
    PartyContact,
    PartyIdentifier,
    PartyRelationship,
    Person,
)
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.jobs import Job, register_job_handler
from xolotl.platform.storage import ObjectRef, ObjectStore

logger = logging.getLogger(__name__)

RETENTION_SWEEP_KIND = "party.retention_sweep"
PURGE_BATCH = 100


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """A purged party and the objects of its documents, deleted after the commit."""

    party_id: UUID
    documents: tuple[ObjectRef, ...]


def _due(today: date) -> tuple[ColumnElement[bool], ...]:
    return (
        Party.status == PartyStatus.ACTIVE,
        Party.retention_until < today,
        Party.legal_hold.is_(False),
        ~exists(
            select(CustomerRelationship.id).where(
                CustomerRelationship.party_id == Party.id,
                CustomerRelationship.status.in_(OPEN_STATUSES),
            )
        ),
    )


def due_parties(s: Session, *, today: date, limit: int = PURGE_BATCH) -> list[Party]:
    """Lock the next parties due for purge, skipping those another sweep holds.

    Returns:
        Up to ``limit`` parties, by id.

    """
    query = (
        select(Party)
        .where(*_due(today))
        .order_by(Party.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(s.scalars(query))


def purge_party(s: Session, party: Party) -> PurgeResult:
    """Erase a party's personal data, keeping the row and its relationships.

    Returns:
        The party's id and the refs of the documents whose objects must go.

    """
    person = s.get(Person, (party.tenant_id, party.id))
    if person is not None:
        person.given_names = None
        person.first_surname = None
        person.second_surname = None
        person.birth_date = None
        person.clave_elector = None
    organization = s.get(Organization, (party.tenant_id, party.id))
    if organization is not None:
        organization.legal_name = None
    rows = s.scalars(select(Document).where(Document.party_id == party.id)).all()
    refs = tuple(document_ref(row) for row in rows)
    identifiers = s.scalar(
        select(func.count())
        .select_from(PartyIdentifier)
        .where(PartyIdentifier.party_id == party.id)
    )
    for model in (PartyIdentifier, PartyContact, PartyAddress, Document):
        _ = s.execute(delete(model).where(model.party_id == party.id))
    _ = s.execute(
        delete(PartyRelationship).where(
            (PartyRelationship.from_party_id == party.id)
            | (PartyRelationship.to_party_id == party.id)
        )
    )
    party.status = PartyStatus.PURGED
    party.updated_at = clock.now()
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="party.purged",
            entity_type="party",
            entity_id=str(party.id),
            detail={"documents": len(refs), "identifiers": identifiers or 0},
        ),
    )
    _ = publish_party_event(
        s,
        PARTY_PURGED,
        entity="party",
        entity_id=party.id,
        data={"party_id": str(party.id)},
    )
    return PurgeResult(party_id=party.id, documents=refs)


class RetentionSweeper:
    """Purges a tenant's due parties batch by batch, then deletes their objects."""

    def __init__(self, object_store: ObjectStore) -> None:
        self._store: ObjectStore = object_store

    def sweep(self, session: Session, *, today: date, dry_run: bool = False) -> int:
        """Purge every party of the session's tenant that is due on ``today``.

        Each batch commits before its objects are deleted; the session carries on
        in a new transaction under the same tenant context.

        Returns:
            How many parties were purged, or with ``dry_run`` how many are due.

        """
        if dry_run:
            count = session.scalar(
                select(func.count()).select_from(Party).where(*_due(today))
            )
            return count or 0
        purged = 0
        while True:
            batch = due_parties(session, today=today)
            results = [purge_party(session, party) for party in batch]
            session.commit()
            purged += len(results)
            for result in results:
                for ref in result.documents:
                    self._delete(ref)
            if len(batch) < PURGE_BATCH:
                return purged

    def _delete(self, ref: ObjectRef) -> None:
        try:
            self._store.delete(ref)
        except Exception:
            logger.warning(
                "purged document not deleted",
                extra={"tenant_id": str(ref.tenant_id), "key": ref.key},
                exc_info=True,
            )

    def handle(self, session: Session, _job: Job) -> None:
        """Run a ``party.retention_sweep`` job for the job's tenant."""
        _ = self.sweep(session, today=clock.now().date())


def register_party(*, object_store: ObjectStore) -> None:
    """Register the retention sweep's job handler with the platform's job queue."""
    register_job_handler(RETENTION_SWEEP_KIND, RetentionSweeper(object_store).handle)
