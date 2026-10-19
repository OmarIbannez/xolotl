"""What a connector is asked, what it answers and how its calls fail (spec §8)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from xolotl.platform.enums import CallOutcome, CheckType, ConnectorMode, ConnectorRoute


@dataclass(frozen=True, slots=True)
class ConnectorRequest:
    """One check to run: its type, its subject, its parameters and the country."""

    check_type: CheckType
    subject_party_id: UUID | None
    params: Mapping[str, str]
    country: str = "MX"


@dataclass(frozen=True, slots=True)
class RawResponse:
    """The remote's answer to one attempt, before it is interpreted."""

    status: int
    body: bytes
    headers: Mapping[str, str]
    elapsed_ms: int


class CheckResult(BaseModel):
    """A connector's reading of a raw answer."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    outcome: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    details: dict[str, JsonValue]
    cost_micros: int = 0


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    """What ``Connector.run`` returns: the outcome and how it was reached."""

    call_id: UUID
    connector: str
    check_type: CheckType
    mode: ConnectorMode
    route: ConnectorRoute
    outcome: CallOutcome
    details: dict[str, JsonValue]
    evidence_key: str | None
    duration_ms: int
    cost_micros: int
    attempts: int
    error: str | None


class ConnectorCallError(Exception):
    """A failure inside a connector's call, interpretation or mock.

    It is raised inside ``_call``, ``_interpret`` and ``_mock`` and never leaves
    ``Connector.run``, which turns it into an outcome.
    """


class TransientCallError(ConnectorCallError):
    """A failure worth retrying; each one counts a circuit breaker failure."""


class PermanentCallError(ConnectorCallError):
    """A failure that ends the call at once and counts no breaker failure."""


class CredentialMissingError(PermanentCallError):
    """The connector needs a credential the vault does not hold."""


class InterpretationError(PermanentCallError):
    """The connector could not read the remote's answer."""
