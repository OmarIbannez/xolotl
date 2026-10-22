"""The monitoring operations API (SP-04 spec §15, plan 4a A10).

``POST /v1/monitoring/backfill`` (MLRO, ADMIN) audits and queues a backfill of the
held rows of a range of Mexico City days; ``GET /v1/monitoring/status`` (ADMIN) reads
the processing counters. Both are staff only. Plan 4c appends ``GET /metrics`` (C6)
and ``GET /profiles/{relationship_id}`` (C8): a relationship's last twelve
behaviour profiles, newest first, for every staff role and no API key. The
metrics of a month name rules and count hits and captures, so they are for every
staff role and no API key either (tipping-off, D20; plan 4c Deviation 15).
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request

from xolotl.modules.monitoring.process import status
from xolotl.modules.monitoring.process.backfill import request_backfill
from xolotl.modules.monitoring.profiles.job import recent_profiles
from xolotl.modules.monitoring.profiles.metrics import metrics_for_period
from xolotl.modules.monitoring.schemas import (
    BackfillAccepted,
    BackfillRequest,
    BehaviourProfileResponse,
    MetricsResponse,
    MonitoringStatus,
)
from xolotl.platform import clock
from xolotl.platform.api.app import app_services
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.enums import Role
from xolotl.platform.jobs.queue import PostgresJobQueue

router = v1_router(prefix="/monitoring", tags=["monitoring"])

BackfillWriter = Annotated[
    Actor, Depends(require_access(roles=(Role.MLRO, Role.ADMIN), write=True))
]
StatusReader = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,)))]
# Staff only: a route without a scope refuses every API client.
StaffReader = Annotated[Actor, Depends(require_access())]


def backfill_queue(request: Request) -> PostgresJobQueue:
    """Give the backfill route the job queue its application was composed with.

    Returns:
        The process's queue; ``enqueue`` uses only the session it is given.

    """
    return app_services(request).queue


QueueDep = Annotated[PostgresJobQueue, Depends(backfill_queue)]


@router.post("/backfill", status_code=202)
def request_backfill_endpoint(
    body: BackfillRequest, _writer: BackfillWriter, session: SessionDep, queue: QueueDep
) -> BackfillAccepted:
    job_id = request_backfill(
        session, from_=body.from_, to=body.to, party_id=body.party_id, queue=queue
    )
    return BackfillAccepted(job_id=job_id)


@router.get("/status")
def monitoring_status(_admin: StatusReader, session: SessionDep) -> MonitoringStatus:
    return status.monitoring_status(session, now=clock.now())


@router.get("/profiles/{relationship_id}")
def behaviour_profiles(
    relationship_id: UUID, _staff: StaffReader, session: SessionDep
) -> list[BehaviourProfileResponse]:
    return [
        BehaviourProfileResponse.model_validate(row)
        for row in recent_profiles(session, relationship_id)
    ]


@router.get("/metrics")
def get_monitoring_metrics(
    period: str,
    _staff: StaffReader,
    session: SessionDep,
    rule_version_id: UUID | None = None,
) -> MetricsResponse:
    """Compute the effectiveness metrics of a Mexico City month (``YYYY-MM``).

    Returns:
        The month's hits, typology coverage, captures and decision latencies.

    """
    return metrics_for_period(session, period=period, rule_version_id=rule_version_id)
