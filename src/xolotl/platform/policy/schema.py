"""Policy pack schema v1 (spec §5.1).

Every regulatory value (a threshold, deadline, period, list name or legal switch)
is a ``Provenanced`` value that carries its legal basis and whether that basis was
verified. ``unverified_paths`` names every value still ``UNVERIFIED`` so the loader
can refuse it. Amounts, percentages and bands are ``PlainDecimal`` and dump as
strings, so a pack's JSON never holds a float.
"""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import ClassVar, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from xolotl.platform.calendar.deadlines import DeadlineRule
from xolotl.platform.enums import Confidence
from xolotl.platform.policy.durations import IsoDuration
from xolotl.platform.units.quantity import PlainDecimal, Quantity
from xolotl.platform.units.rules import ConversionRule

_STRICT = ConfigDict(frozen=True, extra="forbid")


class Provenanced[T](BaseModel):
    """A regulatory value with the legal basis it comes from."""

    model_config: ClassVar[ConfigDict] = _STRICT

    value: T
    unit: str | None = None
    legal_basis: str = Field(min_length=1)
    confidence: Confidence
    verified_as_of: date


class TierLimits(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    monthly_ops: Provenanced[Quantity] | None = None
    max_balance: Provenanced[Quantity] | None = None


class KycTier(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    code: str
    limits: TierLimits
    required_fields: list[str]
    required_checks: list[str]
    assurance: str


class KycPolicy(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    tiers: list[KycTier]
    review_frequency: dict[str, Provenanced[IsoDuration]]
    geolocation_required: Provenanced[bool]


class UboPolicy(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    ownership_threshold_pct: Provenanced[PlainDecimal]
    control_tests: list[str]
    fallback: Literal["SENIOR_MANAGING_OFFICIAL"]
    legal_person_can_be_ubo: bool = False


class ScreeningBands(BaseModel):
    # validate_default runs the defaults through PlainDecimal, so a pack that omits
    # a band dumps it exactly as one that writes the same value.
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", validate_default=True
    )

    sanctions_alert: PlainDecimal = Decimal("0.70")
    sanctions_review: PlainDecimal = Decimal("0.55")
    pep_alert: PlainDecimal = Decimal("0.80")
    id_match_floor: PlainDecimal = Decimal("0.95")
    id_conflict_cap: PlainDecimal = Decimal("0.30")


class PepPolicy(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    # Keyed DOMESTIC, FOREIGN, INTL_ORG; a null value means no cooling-off: once a
    # PEP, a PEP for ever.
    cooling_off: dict[str, Provenanced[IsoDuration | None]]


class TrueMatchAction(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    action: Literal["SUSPEND_IMMEDIATELY", "REVIEW"]
    report: str | None = None
    notify_client: bool = False


class ScreeningPolicy(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    mandatory_lists: list[Provenanced[str]]
    recommended_lists: list[str]
    bands: ScreeningBands = ScreeningBands()
    pep: PepPolicy
    on_true_match: dict[str, TrueMatchAction]


class Capture(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    code: str
    kind: Literal["THRESHOLD", "ACCUMULATION", "FEE_THRESHOLD"]
    threshold: Provenanced[Quantity]
    conversion_rule: ConversionRule
    window: Provenanced[IsoDuration] | None = None
    period: Provenanced[Literal["MONTH", "QUARTER"]]
    report: str
    filing: Literal["SERIALIZED", "MANUAL_UPLOAD"]
    # Another capture of the same pack that files one report for an operation
    # both trigger.
    merge_into: Provenanced[str] | None = None


class ReportRef(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    code: str
    anchor: Literal["period_end", "committee_decision", "detection"]
    deadline: Provenanced[DeadlineRule]
    zero_report: bool


class RetentionPolicy(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    kyc_after_relationship: Provenanced[IsoDuration]
    transactions: Provenanced[IsoDuration]


class GovernancePolicy(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    committee_required: Provenanced[bool]
    four_eyes_on_reports: bool = True


class UnitsPolicy(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    quote_ccy: Literal["MXN"] = "MXN"
    tier_limit_rule: Provenanced[ConversionRule] | None = None


def _check_unique(kind: str, codes: list[str]) -> None:
    repeated = sorted({code for code in codes if codes.count(code) > 1})
    if repeated:
        msg = f"{kind} codes must be unique: {', '.join(repeated)} repeated"
        raise ValueError(msg)


class PolicyBody(BaseModel):
    """What a pack regulates; ``PackConfig`` adds the pack's identity and dates."""

    model_config: ClassVar[ConfigDict] = _STRICT

    units: UnitsPolicy
    kyc: KycPolicy
    ubo: UboPolicy
    screening: ScreeningPolicy
    captures: list[Capture]
    reports: list[ReportRef]
    retention: RetentionPolicy
    governance: GovernancePolicy

    @model_validator(mode="after")
    def _codes_are_consistent(self) -> Self:
        _check_unique("tier", [tier.code for tier in self.kyc.tiers])
        captures = [capture.code for capture in self.captures]
        _check_unique("capture", captures)
        _check_unique("report", [report.code for report in self.reports])
        for capture in self.captures:
            target = capture.merge_into
            if target is not None and (
                target.value == capture.code or target.value not in captures
            ):
                msg = (
                    f"captures[{capture.code}].merge_into must name another capture "
                    f"of the pack, not {target.value}"
                )
                raise ValueError(msg)
        return self


class PackConfig(PolicyBody):
    """One version of a pack, as its YAML file writes it."""

    pack: str = Field(pattern=r"^[A-Z]{2}/[A-Z0-9]+-[A-Z0-9]+$")
    version: int = Field(ge=1)
    title: str = Field(min_length=1)
    effective_from: date
    effective_to: date | None = None
    country: str = Field(pattern=r"^[A-Z]{2}$")
    regulators: list[str] = Field(min_length=1)
    products: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _header_is_consistent(self) -> Self:
        if not self.pack.startswith(f"{self.country}/"):
            msg = f"pack {self.pack} must start with its country {self.country}/"
            raise ValueError(msg)
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            msg = "effective_to must be after effective_from"
            raise ValueError(msg)
        return self


def _key_of(item: object, index: int) -> str:
    # List items are named by their code (tiers, captures, reports) or, for a list
    # of provenanced names, by the name; anything else by its position.
    if isinstance(item, Provenanced):
        return str(cast("object", item.value))
    code = cast("object", getattr(item, "code", None))
    return code if isinstance(code, str) else str(index)


def _walk(value: object, path: str) -> Iterator[tuple[str, Provenanced[object]]]:
    if isinstance(value, Provenanced):
        yield path, cast("Provenanced[object]", value)
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            child = cast("object", getattr(value, name))
            yield from _walk(child, f"{path}.{name}" if path else name)
    elif isinstance(value, list):
        items = cast("list[object]", value)
        for index, item in enumerate(items):
            yield from _walk(item, f"{path}[{_key_of(item, index)}]")
    elif isinstance(value, dict):
        entries = cast("dict[object, object]", value)
        for key, item in entries.items():
            yield from _walk(item, f"{path}[{key}]")


def unverified_paths(body: PolicyBody) -> list[str]:
    """Name every provenanced value whose legal basis is still unverified.

    Returns:
        The sorted dotted paths, such as ``captures[ITF_ROR_CASH].period`` or
        ``kyc.review_frequency[HIGH]``; empty when everything is verified.

    """
    return sorted(
        path
        for path, value in _walk(body, "")
        if value.confidence is Confidence.UNVERIFIED
    )
