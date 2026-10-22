"""What the staff counterparties routes show (SP-04 plan 4c, C3; spec §15, §17).

Masked: a list row and the detail carry the initials of the name and the last four
characters of the account, the wallet address (normalized as the matcher compares
it) and the identifier. Computing them decrypts the row for processing (D6, as the
screening queue's subjects are masked), which is not a reveal and audits nothing.
Revealed: the plaintext values come only from A10's ``views.reveal_counterparty``,
which records one ``VIEW_PII`` per value before it decrypts any. The detail adds the
counterparty subject's findings, newest first, and its last ten runs. Every query
runs in the session's tenant, so another tenant's counterparty is not found. These
views are staff only: an API client never learns a counterparty's screening state
(tipping-off, spec §15).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from xolotl.modules.party.masking import initials, mask_identifier
from xolotl.modules.screening.api import match_summaries
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyKind,
    CounterpartyScreeningStatus,
    normalize_wallet,
)
from xolotl.modules.screening.counterparty.schemas import (
    CounterpartyDetail,
    CounterpartyResponse,
)
from xolotl.modules.screening.counterparty.views import counterparty_summary
from xolotl.modules.screening.engine.models import (
    ScreeningMatch,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.schemas import RunResponse
from xolotl.platform.crypto.encrypted import Encrypted
from xolotl.platform.db import bound_context
from xolotl.platform.errors import NotFoundError

RUNS_SHOWN = 10


def counterparties_query(
    *,
    status: CounterpartyScreeningStatus | None,
    kind: CounterpartyKind | None,
    seen_after: datetime | None,
) -> Select[Counterparty]:
    """Select the tenant's counterparties that pass the list filters.

    ``seen_after`` keeps the counterparties last seen at or after that instant.

    Returns:
        The statement, unordered; the caller paginates it by ``last_seen_at``.

    """
    query = select(Counterparty)
    if status is not None:
        query = query.where(Counterparty.screening_status == status)
    if kind is not None:
        query = query.where(Counterparty.kind == kind)
    if seen_after is not None:
        query = query.where(Counterparty.last_seen_at >= seen_after)
    return query


def _tail(s: Session, value: Encrypted[str] | None) -> str | None:
    # Decrypted for processing: only the masked tail leaves this function.
    return None if value is None else mask_identifier(value.reveal(session=s))


def _wallet_tail(s: Session, row: Counterparty) -> str | None:
    if row.wallet is None:
        return None
    address = normalize_wallet(row.wallet.reveal(session=s))
    return None if address is None else mask_identifier(address)


def counterparty_row(s: Session, row: Counterparty) -> CounterpartyResponse:
    """Build a counterparty's masked list row.

    Returns:
        A10's summary plus the initials, the masked tails, the screening stamp, the
        subject and the first and last sightings.

    """
    summary = counterparty_summary(s, row.id)
    return CounterpartyResponse.model_validate({
        **summary.model_dump(),
        "initials": "" if row.name is None else initials(row.name.reveal(session=s)),
        "account_masked": _tail(s, row.account),
        "wallet_masked": _wallet_tail(s, row),
        "identifier_masked": _tail(s, row.identifier),
        "identifier_type": (
            None if row.identifier_type is None else row.identifier_type.value
        ),
        "screened_at": row.screened_at,
        "subject_id": row.screening_subject_id,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
    })


def _runs(s: Session, subject: ScreeningSubject) -> list[RunResponse]:
    runs = s.scalars(
        select(ScreeningRun)
        .where(ScreeningRun.subject_id == subject.id)
        .order_by(ScreeningRun.created_at.desc(), ScreeningRun.id.desc())
        .limit(RUNS_SHOWN)
    )
    return [
        RunResponse(
            id=run.id,
            subject_id=subject.id,
            party_id=None,
            mode=run.mode,
            outcome=run.outcome,
            created_at=run.created_at,
        )
        for run in runs
    ]


def counterparty_detail(s: Session, counterparty_id: UUID) -> CounterpartyDetail:
    """Build a counterparty's masked detail with its findings and runs.

    Returns:
        The list row, the subject's findings newest first and its last ten runs;
        both are empty before the counterparty's first screening.

    Raises:
        NotFoundError: When the session's tenant has no such counterparty.

    """
    tenant_id = bound_context(s).require_tenant_id()
    row = s.get(Counterparty, (tenant_id, counterparty_id))
    if row is None:
        msg = "counterparty not found"
        raise NotFoundError(msg)
    base = counterparty_row(s, row)
    subject = s.scalars(
        select(ScreeningSubject).where(ScreeningSubject.counterparty_id == row.id)
    ).one_or_none()
    findings: list[ScreeningMatch] = []
    runs: list[RunResponse] = []
    if subject is not None:
        findings = list(
            s.scalars(
                select(ScreeningMatch)
                .where(ScreeningMatch.subject_id == subject.id)
                .order_by(ScreeningMatch.created_at.desc(), ScreeningMatch.id.desc())
            )
        )
        runs = _runs(s, subject)
    return CounterpartyDetail.model_validate({
        **base.model_dump(),
        "findings": match_summaries(s, findings),
        "runs": runs,
    })
