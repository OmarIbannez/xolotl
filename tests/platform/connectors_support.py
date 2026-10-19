"""The fake connector and the helpers every connector test imports."""

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import override
from uuid import UUID

from sqlalchemy import select

from tests.helpers import system_ctx
from xolotl.platform.connectors import (
    CheckResult,
    CheckType,
    Connector,
    ConnectorDeps,
    ConnectorRequest,
    CredentialVault,
    RawResponse,
    VerificationOutcome,
)
from xolotl.platform.connectors.models import ConnectorCall
from xolotl.platform.db import tenant_session
from xolotl.platform.storage.local import LocalFsObjectStore

RFC_ONLY: frozenset[CheckType] = frozenset({CheckType.RFC_VALIDATION})


class FakeMonotonic:
    """A monotonic clock that moves only when told to."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now: float = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingSleep:
    """A ``sleep_fn`` that records the backoff instead of sleeping."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_deps(
    tmp_path: Path, *, clock: FakeMonotonic | None = None
) -> tuple[ConnectorDeps, LocalFsObjectStore, RecordingSleep]:
    store = LocalFsObjectStore(tmp_path / "objects")
    sleep = RecordingSleep()
    deps = ConnectorDeps(
        vault=CredentialVault(),
        store=store,
        clock_fn=clock or FakeMonotonic(),
        sleep_fn=sleep,
    )
    return deps, store, sleep


type Scripted = RawResponse | BaseException


class UnscriptedCallError(BaseException):
    """An attempt the test did not script.

    It is not an ``Exception``, so a run's blind catch cannot turn it into an outcome
    and the test fails loudly.
    """


class FakeConnector(Connector):
    """A connector whose attempts follow a script a test can extend.

    The breakers live on the instance, so a half-open trial must run on the instance
    that opened the breaker: a test extends ``calls`` to script further attempts.
    """

    evidence_content_type = "application/json"

    def __init__(
        self,
        deps: ConnectorDeps,
        *,
        name: str = "fake",
        check_types: frozenset[CheckType] = RFC_ONLY,
        calls: Iterable[Scripted] = (),
        interpret: Callable[[RawResponse], CheckResult] | None = None,
        cost_micros: int = 7,
    ) -> None:
        super().__init__(deps, name=name, check_types=check_types)
        self.calls: list[Scripted] = list(calls)
        self.seen_credentials: list[dict[str, str]] = []
        self.call_count: int = 0
        self._interpreter = interpret
        self._cost_micros = cost_micros

    @override
    def _call(
        self, request: ConnectorRequest, credentials: Mapping[str, str]
    ) -> RawResponse:
        self.seen_credentials.append(dict(credentials))
        self.call_count += 1
        if not self.calls:
            msg = "unscripted call"
            raise UnscriptedCallError(msg)
        step = self.calls.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    @override
    def _interpret(self, raw: RawResponse) -> CheckResult:
        if self._interpreter is not None:
            return self._interpreter(raw)
        return CheckResult(
            outcome="PASS",
            details={"status": raw.status},
            cost_micros=self._cost_micros,
        )

    @override
    def _mock(self, request: ConnectorRequest) -> RawResponse:
        rfc = request.params.get("rfc", "")
        body = f'{{"mock": true, "rfc": "{rfc}"}}'.encode()
        return RawResponse(200, body, {"content-type": "application/json"}, 0)


def ok(body: bytes = b'{"ok": 1}', status: int = 200) -> RawResponse:
    return RawResponse(status, body, {"content-type": "application/json"}, 1)


def evidence_of(
    store_root: Path, tenant_id: UUID, outcome: VerificationOutcome
) -> bytes:
    assert outcome.evidence_key is not None
    return (store_root / str(tenant_id) / outcome.evidence_key).read_bytes()


def calls_of(tenant_id: UUID) -> list[ConnectorCall]:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(
            select(ConnectorCall).order_by(ConnectorCall.created_at, ConnectorCall.id)
        ).all()
        s.expunge_all()
        return list(rows)


def request(rfc: str = "XAXX010101000", **params: str) -> ConnectorRequest:
    return ConnectorRequest(
        check_type=CheckType.RFC_VALIDATION,
        subject_party_id=None,
        params={"rfc": rfc, **params},
    )
