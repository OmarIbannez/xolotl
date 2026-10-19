"""The ``banxico_sie`` connector: a UDI or FIX value on a day, from Banxico's SIE.

The SIE HTTP call belongs to plan 2a's ``BanxicoSieSource``; this connector sees it
only as a fetch callable that answers ``(date, value)`` pairs, built per token by the
factory ``defaults.py`` holds. Its evidence is those observations as canonical JSON.

A tenant's vault token wins over the platform's ``XOLOTL_BANXICO_TOKEN``. With
neither, a call outside production answers from the committed seed files (demo mode,
the same body the sandbox mock gives) and a call in production is refused.
"""

import operator
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import ClassVar, override

from pydantic import BaseModel, Field, ValidationError

from xolotl.platform.audit import canonical_json
from xolotl.platform.connectors.base import Connector, ConnectorDeps
from xolotl.platform.connectors.types import (
    CheckResult,
    ConnectorRequest,
    CredentialMissingError,
    InterpretationError,
    PermanentCallError,
    RawResponse,
    TransientCallError,
)
from xolotl.platform.enums import CheckType
from xolotl.platform.errors import XolotlError
from xolotl.platform.units import UnitSourceAuthError

BANXICO_SIE = "banxico_sie"
UNIT_CODES = frozenset({"UDI", "USD_MXN_FIX"})
LOOKBACK_DAYS = 10
MOCK_VALUES: Mapping[str, str] = {"UDI": "8.500000", "USD_MXN_FIX": "18.500000"}

type ObservationPairs = Sequence[tuple[date, Decimal]]
type ObservationFetch = Callable[[str, date, date], ObservationPairs]
type SeedLookup = Callable[[str, date], Decimal | None]

_JSON_HEADERS: Mapping[str, str] = {"content-type": "application/json"}


@dataclass(frozen=True, slots=True)
class UnitQuery:
    """A unit and the day its value is asked for."""

    unit_code: str
    on: date


def unit_query_from(params: Mapping[str, str]) -> UnitQuery:
    """Read a request's ``unit_code`` and ``on``.

    Returns:
        The query.

    Raises:
        PermanentCallError: When ``unit_code`` is missing or not one of
            ``UNIT_CODES``, or ``on`` is missing or not an ISO date; the message
            names the key.

    """
    unit_code = (params.get("unit_code") or "").strip()
    if unit_code not in UNIT_CODES:
        msg = f"unit_code must be one of {', '.join(sorted(UNIT_CODES))}"
        raise PermanentCallError(msg)
    on = (params.get("on") or "").strip()
    try:
        day = date.fromisoformat(on)
    except ValueError as exc:
        msg = "on must be an ISO date (YYYY-MM-DD)"
        raise PermanentCallError(msg) from exc
    return UnitQuery(unit_code, day)


def observations_body(query: UnitQuery, rows: ObservationPairs) -> bytes:
    """Write the observations as the call's evidence.

    Returns:
        Canonical JSON with the unit, the day and the observations sorted by date.

    """
    return canonical_json({
        "unit_code": query.unit_code,
        "on": query.on.isoformat(),
        "observations": [
            {"date": day.isoformat(), "value": str(value)}
            for day, value in sorted(rows, key=operator.itemgetter(0))
        ],
    })


# The evidence body as ``observations_body`` writes it; values stay text, so the
# outcome carries the digits the source gave.
class _Observation(BaseModel):
    day: date = Field(alias="date")
    value: Decimal


class _Body(BaseModel):
    unit_code: str
    on: date
    observations: list[_Observation]


def _observations(body: bytes) -> tuple[str, date, list[tuple[date, str]]]:
    try:
        parsed = _Body.model_validate_json(body)
    except ValidationError as exc:
        msg = "the Banxico observations are unreadable"
        raise InterpretationError(msg) from exc
    rows = [(item.day, str(item.value)) for item in parsed.observations]
    return parsed.unit_code, parsed.on, rows


def interpret_body(body: bytes) -> CheckResult:
    """Read the observations body.

    Returns:
        ``PASS`` with the latest observation on or before the day asked for, or
        ``INCONCLUSIVE`` (reason ``NO_OBSERVATION``) when there is none. A body
        that is not the observations JSON is an ``InterpretationError``.

    """
    unit_code, on, rows = _observations(body)
    prior = [row for row in rows if row[0] <= on]
    if not prior:
        return CheckResult(
            outcome="INCONCLUSIVE",
            details={
                "reason": "NO_OBSERVATION",
                "unit_code": unit_code,
                "on": on.isoformat(),
            },
        )
    value_date, value = max(prior, key=operator.itemgetter(0))
    return CheckResult(
        outcome="PASS",
        details={
            "unit_code": unit_code,
            "on": on.isoformat(),
            "value": value,
            "value_date": value_date.isoformat(),
            "source": BANXICO_SIE,
        },
    )


class BanxicoSieConnector(Connector):
    """``UNIT_VALUE`` (UDI, FIX) through plan 2a's Banxico SIE source."""

    evidence_content_type: ClassVar[str] = "application/json"

    def __init__(
        self,
        deps: ConnectorDeps,
        *,
        fetch_factory: Callable[[str], ObservationFetch],
        seed_lookup: SeedLookup,
        platform_token: str | None,
        prod: bool,
    ) -> None:
        super().__init__(
            deps, name=BANXICO_SIE, check_types=frozenset({CheckType.UNIT_VALUE})
        )
        self._fetch_factory: Callable[[str], ObservationFetch] = fetch_factory
        self._seed_lookup: SeedLookup = seed_lookup
        self._platform_token: str | None = platform_token
        self._prod: bool = prod

    @override
    def _call(
        self, request: ConnectorRequest, credentials: Mapping[str, str]
    ) -> RawResponse:
        token = credentials.get("token") or self._platform_token
        if not token:
            if self._prod:
                msg = "banxico_sie has no SIE token"
                raise CredentialMissingError(msg)
            # Demo mode: the mock body travels the live path, so the route stays
            # the one the router passed.
            return self._mock(request)
        query = unit_query_from(request.params)
        fetch = self._fetch_factory(token)
        started = self._deps.clock_fn()
        try:
            rows = fetch(
                query.unit_code, query.on - timedelta(days=LOOKBACK_DAYS), query.on
            )
        except UnitSourceAuthError as exc:
            # A rejected token does not heal with backoff.
            raise PermanentCallError(str(exc)) from exc
        except XolotlError as exc:
            raise TransientCallError(str(exc)) from exc
        elapsed_ms = int((self._deps.clock_fn() - started) * 1000)
        return RawResponse(
            200, observations_body(query, rows), _JSON_HEADERS, elapsed_ms
        )

    @override
    def _interpret(self, raw: RawResponse) -> CheckResult:
        return interpret_body(raw.body)

    @override
    def _mock(self, request: ConnectorRequest) -> RawResponse:
        query = unit_query_from(request.params)
        value = self._seed_lookup(query.unit_code, query.on)
        if value is None:
            value = Decimal(MOCK_VALUES[query.unit_code])
        return RawResponse(
            200, observations_body(query, [(query.on, value)]), _JSON_HEADERS, 0
        )
