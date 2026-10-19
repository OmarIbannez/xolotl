"""The ``Connector`` base: the one way a connector is run (spec §8).

``Connector.run`` forces the mock for a ``SANDBOX`` call, reads the credentials from
the vault, consults the cell's circuit breaker, makes up to ``max_attempts`` attempts
with a fixed backoff, stores the last raw answer as evidence with every credential
value redacted, writes one ``connector_call`` row in the caller's session and returns
a ``VerificationOutcome``. It never raises: a failure is an outcome.
"""

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import ClassVar
from uuid import UUID

import httpx
from pydantic import JsonValue
from sqlalchemy.orm import Session

from xolotl.platform.connectors.breaker import CircuitBreaker
from xolotl.platform.connectors.models import ConnectorCall
from xolotl.platform.connectors.types import (
    CheckResult,
    ConnectorRequest,
    RawResponse,
    TransientCallError,
    VerificationOutcome,
)
from xolotl.platform.connectors.vault import CredentialVault
from xolotl.platform.db import bound_context
from xolotl.platform.enums import (
    CallOutcome,
    CheckType,
    CircuitState,
    ConnectorMode,
    ConnectorRoute,
    TenantEnv,
)
from xolotl.platform.ids import uuid7
from xolotl.platform.logging import safe_exception_summary
from xolotl.platform.storage import ObjectStore
from xolotl.platform.tenancy.tenants import tenant_runtime

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 8.0
DEFAULT_MAX_ATTEMPTS = 3
BACKOFF_S: tuple[float, ...] = (0.2, 0.5)
EVIDENCE_PREFIX = "connectors"
REDACTED = "[REDACTED]"


def _secrets_longest_first(secrets: Iterable[str]) -> list[str]:
    return sorted({s for s in secrets if s}, key=len, reverse=True)


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every secret in ``text``, the longest first; empty secrets are skipped.

    Returns:
        ``text`` with each secret replaced by ``[REDACTED]``.

    """
    for secret in _secrets_longest_first(secrets):
        text = text.replace(secret, REDACTED)
    return text


def redact_bytes(body: bytes, secrets: Iterable[str]) -> bytes:
    """Replace every secret's UTF-8 bytes in ``body``, the longest first.

    Returns:
        ``body`` with each secret replaced by ``[REDACTED]``.

    """
    for secret in _secrets_longest_first(secrets):
        body = body.replace(secret.encode(), REDACTED.encode())
    return body


def redact_details(
    details: dict[str, JsonValue], secrets: Iterable[str]
) -> dict[str, JsonValue]:
    """Redact every top-level string value of ``details``; other values stay.

    Returns:
        A new mapping with the same keys.

    """
    found = _secrets_longest_first(secrets)
    return {
        k: redact(v, found) if isinstance(v, str) else v for k, v in details.items()
    }


@dataclass(frozen=True, slots=True)
class ConnectorDeps:
    """What every connector is built with; tests swap the clock and the sleep."""

    vault: CredentialVault
    store: ObjectStore
    clock_fn: Callable[[], float] = time.monotonic
    sleep_fn: Callable[[float], None] = time.sleep


@dataclass(slots=True)
class _Tally:
    """How the attempts (or the mock) of one run went."""

    raw: RawResponse | None = None
    answered: bool = False
    kind: CallOutcome = CallOutcome.ERROR
    error: str | None = None
    attempts: int = 0


@dataclass(slots=True)
class _Verdict:
    """The outcome of one run before it is metered."""

    outcome: CallOutcome
    error: str | None
    details: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    cost_micros: int = 0


class Connector(ABC):
    """An external verification service behind timeouts, retries and a breaker."""

    timeout_s: ClassVar[float] = DEFAULT_TIMEOUT_S
    max_attempts: ClassVar[int] = DEFAULT_MAX_ATTEMPTS
    evidence_content_type: ClassVar[str] = "application/octet-stream"

    def __init__(
        self, deps: ConnectorDeps, *, name: str, check_types: frozenset[CheckType]
    ) -> None:
        self.name: str = name
        self.check_types: frozenset[CheckType] = check_types
        self._deps: ConnectorDeps = deps
        self._breakers: dict[str, CircuitBreaker] = {}

    def breaker(self, cell: str) -> CircuitBreaker:
        """Return the breaker of ``cell``, creating it on first use.

        Returns:
            The cell's breaker, on the deps' clock.

        """
        found = self._breakers.get(cell)
        if found is None:
            found = CircuitBreaker(clock_fn=self._deps.clock_fn)
            self._breakers[cell] = found
        return found

    def circuit_state(self, cell: str) -> CircuitState:
        """Report the state of the breaker of ``cell`` without creating one.

        Returns:
            The breaker's state, or ``CLOSED`` when the cell has no breaker yet.

        """
        found = self._breakers.get(cell)
        return CircuitState.CLOSED if found is None else found.state

    @abstractmethod
    def _call(
        self, request: ConnectorRequest, credentials: Mapping[str, str]
    ) -> RawResponse: ...

    @abstractmethod
    def _interpret(self, raw: RawResponse) -> CheckResult: ...

    @abstractmethod
    def _mock(self, request: ConnectorRequest) -> RawResponse: ...

    def run(
        self,
        session: Session,
        request: ConnectorRequest,
        *,
        mode: ConnectorMode,
        route: ConnectorRoute = ConnectorRoute.PRIMARY,
    ) -> VerificationOutcome:
        """Run one check and meter it; a failure is an outcome, never an exception.

        A ``SANDBOX`` tenant is always a ``SANDBOX`` call, and a ``SANDBOX`` call runs
        the mock without reading the vault, on route ``MOCK``.

        Returns:
            The outcome, with the call id, the evidence key and the metering figures.

        """
        tenant_id = bound_context(session).require_tenant_id()
        runtime = tenant_runtime(session, tenant_id)
        call_id = uuid7()
        started = self._deps.clock_fn()
        if runtime.env is TenantEnv.SANDBOX:
            mode = ConnectorMode.SANDBOX
        credentials: Mapping[str, str] = {}
        if mode is ConnectorMode.SANDBOX:
            route = ConnectorRoute.MOCK
            tally = self._mocked(request)
        else:
            credentials = self._deps.vault.get(session, self.name, mode)
            tally = self._attempts(request, credentials, self.breaker(runtime.cell))
        secrets = _secrets_longest_first(credentials.values())
        verdict = self._interpreted(tally, route, secrets)
        evidence_key: str | None = None
        if tally.raw is not None:
            try:
                evidence_key = self._store_evidence(
                    tenant_id, call_id, tally.raw, secrets
                )
            # A connector must never take the caller's transaction down.
            except Exception as exc:  # ruff: ignore[blind-except]
                verdict = _Verdict(
                    CallOutcome.ERROR,
                    redact(safe_exception_summary(exc), secrets),
                    cost_micros=verdict.cost_micros,
                )
        duration_ms = int((self._deps.clock_fn() - started) * 1000)
        self._meter(
            session,
            tenant_id=tenant_id,
            call_id=call_id,
            request=request,
            mode=mode,
            route=route,
            verdict=verdict,
            evidence_key=evidence_key,
            duration_ms=duration_ms,
        )
        logger.info(
            "connector call",
            extra={
                "connector": self.name,
                "call_id": str(call_id),
                "outcome": verdict.outcome.value,
                "route": route.value,
                "attempts": tally.attempts,
                "duration_ms": duration_ms,
            },
        )
        return VerificationOutcome(
            call_id=call_id,
            connector=self.name,
            check_type=request.check_type,
            mode=mode,
            route=route,
            outcome=verdict.outcome,
            details=verdict.details,
            evidence_key=evidence_key,
            duration_ms=duration_ms,
            cost_micros=verdict.cost_micros,
            attempts=tally.attempts,
            error=verdict.error,
        )

    def _mocked(self, request: ConnectorRequest) -> _Tally:
        tally = _Tally()
        try:
            tally.raw = self._mock(request)
        # A connector must never take the caller's transaction down.
        except Exception as exc:  # ruff: ignore[blind-except]
            tally.error = safe_exception_summary(exc)
            return tally
        tally.answered = True
        return tally

    def _attempts(
        self,
        request: ConnectorRequest,
        credentials: Mapping[str, str],
        breaker: CircuitBreaker,
    ) -> _Tally:
        tally = _Tally()
        if not breaker.allow():
            tally.kind = CallOutcome.CIRCUIT_OPEN
            tally.error = "circuit open"
            return tally
        for attempt in range(1, self.max_attempts + 1):
            tally.attempts = attempt
            if not self._attempt(request, credentials, breaker, tally):
                break
            if attempt < self.max_attempts:
                self._deps.sleep_fn(BACKOFF_S[min(attempt, len(BACKOFF_S)) - 1])
        return tally

    def _attempt(
        self,
        request: ConnectorRequest,
        credentials: Mapping[str, str],
        breaker: CircuitBreaker,
        tally: _Tally,
    ) -> bool:
        """Make one attempt and record it in ``tally``.

        Returns:
            True when the attempt failed in a way worth retrying.

        """
        try:
            raw = self._call(request, credentials)
        # A connector must never take the caller's transaction down.
        except Exception as exc:  # ruff: ignore[blind-except]
            return self._failed(exc, credentials, breaker, tally)
        return self._answered(raw, breaker, tally)

    @staticmethod
    def _failed(
        exc: Exception,
        credentials: Mapping[str, str],
        breaker: CircuitBreaker,
        tally: _Tally,
    ) -> bool:
        # The class alone decides: a timeout, a transport error, an OS error or a
        # transient call error is retried and counts a breaker failure; anything
        # else (a permanent call error included) ends the call at once.
        retry = isinstance(exc, httpx.HTTPError | OSError | TransientCallError)
        tally.kind = (
            CallOutcome.TIMEOUT
            if isinstance(exc, httpx.TimeoutException)
            else CallOutcome.ERROR
        )
        tally.error = redact(safe_exception_summary(exc), credentials.values())
        # The evidence is the last attempt's body, and this attempt has none.
        tally.raw = None
        if retry:
            breaker.record_failure()
        return retry

    @staticmethod
    def _answered(raw: RawResponse, breaker: CircuitBreaker, tally: _Tally) -> bool:
        tally.raw = raw
        if raw.status >= 400:  # ruff: ignore[magic-value-comparison]
            tally.kind, tally.error = CallOutcome.ERROR, f"HTTP {raw.status}"
            if raw.status >= 500:  # ruff: ignore[magic-value-comparison]
                breaker.record_failure()
                return True
            return False
        breaker.record_success()
        tally.answered, tally.error = True, None
        return False

    def _interpreted(
        self, tally: _Tally, route: ConnectorRoute, secrets: list[str]
    ) -> _Verdict:
        if not tally.answered or tally.raw is None:
            return _Verdict(tally.kind, tally.error)
        try:
            result = self._interpret(tally.raw)
        # A connector must never take the caller's transaction down.
        except Exception as exc:  # ruff: ignore[blind-except]
            return _Verdict(
                CallOutcome.ERROR, redact(safe_exception_summary(exc), secrets)
            )
        return _Verdict(
            CallOutcome(result.outcome),
            None,
            details=redact_details(result.details, secrets),
            cost_micros=0 if route is ConnectorRoute.MOCK else result.cost_micros,
        )

    def _store_evidence(
        self, tenant_id: UUID, call_id: UUID, raw: RawResponse, secrets: list[str]
    ) -> str:
        key = f"{EVIDENCE_PREFIX}/{self.name}/{call_id}"
        _ = self._deps.store.put(
            tenant_id,
            key,
            redact_bytes(raw.body, secrets),
            content_type=self.evidence_content_type,
        )
        return key

    def _meter(  # ruff: ignore[too-many-arguments]
        self,
        session: Session,
        *,
        tenant_id: UUID,
        call_id: UUID,
        request: ConnectorRequest,
        mode: ConnectorMode,
        route: ConnectorRoute,
        verdict: _Verdict,
        evidence_key: str | None,
        duration_ms: int,
    ) -> None:
        session.add(
            ConnectorCall(
                tenant_id=tenant_id,
                id=call_id,
                connector=self.name,
                check_type=request.check_type,
                mode=mode,
                route=route,
                outcome=verdict.outcome,
                duration_ms=duration_ms,
                cost_micros=verdict.cost_micros,
                evidence_key=evidence_key,
                error=verdict.error,
                subject_party_id=request.subject_party_id,
            )
        )
        session.flush()
