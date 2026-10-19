"""Tenant overrides: values a tenant may only tighten (spec §5.3).

``validate_against`` proves an override against a merged body when it is created;
``apply_override`` lays it over the body a date and product resolve to. The two
share one notion of "at least as strict": amounts and review periods only lower,
retention only longer (both on the 360-day scale, so ``P1Y`` equals ``P12M``),
lists only added, sanctions and PEP bands only lower, identity bands only higher,
legal switches only from false to true. Equality is allowed everywhere.
"""

import operator
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import ClassVar, NoReturn

from pydantic import BaseModel, ConfigDict

from xolotl.platform.enums import Confidence
from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.policy.durations import IsoDuration, iso_duration_days
from xolotl.platform.policy.errors import PolicyLooseningError
from xolotl.platform.policy.schema import (
    Capture,
    KycTier,
    PolicyBody,
    Provenanced,
    RetentionPolicy,
    ScreeningBands,
    TierLimits,
)
from xolotl.platform.units.quantity import PlainDecimal, Quantity

_STRICT = ConfigDict(frozen=True, extra="forbid")


class TierLimitsOverride(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    monthly_ops: Quantity | None = None
    max_balance: Quantity | None = None


class BandsOverride(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    sanctions_alert: PlainDecimal | None = None
    sanctions_review: PlainDecimal | None = None
    pep_alert: PlainDecimal | None = None
    id_match_floor: PlainDecimal | None = None
    id_conflict_cap: PlainDecimal | None = None


class RetentionOverride(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    kyc_after_relationship: IsoDuration | None = None
    transactions: IsoDuration | None = None


class TenantOverride(BaseModel):
    """What a tenant tightens; every field optional, keyed by code or level."""

    model_config: ClassVar[ConfigDict] = _STRICT

    tier_limits: dict[str, TierLimitsOverride] = {}
    capture_thresholds: dict[str, Quantity] = {}
    review_frequency: dict[str, IsoDuration] = {}
    retention: RetentionOverride | None = None
    mandatory_lists_add: list[str] = []
    recommended_lists_add: list[str] = []
    bands: BandsOverride | None = None
    ubo_ownership_threshold_pct: PlainDecimal | None = None
    committee_required: bool | None = None
    geolocation_required: bool | None = None


type _Holds[T] = Callable[[T, T], bool]


def _limit_pairs(
    override: TierLimitsOverride, limits: TierLimits
) -> tuple[tuple[str, Quantity | None, Provenanced[Quantity] | None], ...]:
    return (
        ("monthly_ops", override.monthly_ops, limits.monthly_ops),
        ("max_balance", override.max_balance, limits.max_balance),
    )


def _retention_pairs(
    override: RetentionOverride, retention: RetentionPolicy
) -> tuple[tuple[str, str | None, Provenanced[str]], ...]:
    return (
        (
            "kyc_after_relationship",
            override.kyc_after_relationship,
            retention.kyc_after_relationship,
        ),
        ("transactions", override.transactions, retention.transactions),
    )


def _band_rows(
    override: BandsOverride, bands: ScreeningBands
) -> tuple[tuple[str, Decimal | None, Decimal, _Holds[Decimal]], ...]:
    # Sanctions and PEP bands only lower, identity bands only higher.
    return (
        (
            "sanctions_alert",
            override.sanctions_alert,
            bands.sanctions_alert,
            operator.le,
        ),
        (
            "sanctions_review",
            override.sanctions_review,
            bands.sanctions_review,
            operator.le,
        ),
        ("pep_alert", override.pep_alert, bands.pep_alert, operator.le),
        ("id_match_floor", override.id_match_floor, bands.id_match_floor, operator.ge),
        (
            "id_conflict_cap",
            override.id_conflict_cap,
            bands.id_conflict_cap,
            operator.ge,
        ),
    )


def _show(value: object) -> str:
    if isinstance(value, Quantity):
        return f"{value.amount} {value.unit}"
    return str(value).lower() if isinstance(value, bool) else str(value)


def _check[T](path: str, new: T, current: T, *, holds: _Holds[T]) -> None:
    if not holds(new, current):
        msg = f"{path}: {_show(new)} loosens {_show(current)}"
        raise PolicyLooseningError(msg)


def _amount_holds(new: Quantity, current: Quantity) -> bool:
    return new.unit == current.unit and new.amount <= current.amount


def _shorter(new: str, current: str) -> bool:
    return iso_duration_days(new) <= iso_duration_days(current)


def _longer(new: str, current: str) -> bool:
    return iso_duration_days(new) >= iso_duration_days(current)


def _switch(new: bool, current: bool) -> bool:  # ruff: ignore[boolean-type-hint-positional-argument]
    return new or not current


def _check_quantity(path: str, new: Quantity, current: Quantity) -> None:
    if new.unit != current.unit:
        msg = f"{path}: {_show(new)} is not in {current.unit}, the pack's unit"
        raise ValidationFailedError(msg)
    _check(path, new, current, holds=_amount_holds)


def _raise_unknown(path: str, what: str) -> NoReturn:
    msg = f"{path}: no such {what} in the assigned packs"
    raise ValidationFailedError(msg)


def _validate_amounts(override: TenantOverride, body: PolicyBody) -> None:
    tiers = {tier.code: tier for tier in body.kyc.tiers}
    for code, limits in override.tier_limits.items():
        tier = tiers.get(code)
        if tier is None:
            _raise_unknown(f"kyc.tiers[{code}]", "tier")
        for name, new, current in _limit_pairs(limits, tier.limits):
            # A limit the pack leaves unlimited may be set to anything.
            if new is not None and current is not None:
                path = f"kyc.tiers[{code}].limits.{name}"
                _check_quantity(path, new, current.value)
    captures = {capture.code: capture for capture in body.captures}
    for code, threshold in override.capture_thresholds.items():
        capture = captures.get(code)
        if capture is None:
            _raise_unknown(f"captures[{code}]", "capture")
        path = f"captures[{code}].threshold"
        _check_quantity(path, threshold, capture.threshold.value)


def _validate_periods(override: TenantOverride, body: PolicyBody) -> None:
    for level, period in override.review_frequency.items():
        path = f"kyc.review_frequency[{level}]"
        current = body.kyc.review_frequency.get(level)
        if current is None:
            _raise_unknown(path, "review level")
        _check(path, period, current.value, holds=_shorter)
    if override.retention is not None:
        for name, new, pack in _retention_pairs(override.retention, body.retention):
            if new is not None:
                _check(f"retention.{name}", new, pack.value, holds=_longer)


def _validate_bands(override: BandsOverride, bands: ScreeningBands) -> None:
    update: dict[str, Decimal] = {}
    for name, value, current, holds in _band_rows(override, bands):
        if value is not None:
            _check(f"screening.bands.{name}", value, current, holds=holds)
            update[name] = value
    result = bands.model_copy(update=update)
    if result.sanctions_review > result.sanctions_alert:
        msg = (
            f"screening.bands: sanctions_review {result.sanctions_review} would "
            f"be above sanctions_alert {result.sanctions_alert}"
        )
        raise ValidationFailedError(msg)


def validate_against(override: TenantOverride, body: PolicyBody) -> None:
    """Prove that ``override`` only tightens ``body``.

    A looser value raises ``PolicyLooseningError`` (409 ``POLICY_LOOSENING``) whose
    detail reads ``"<path>: <new> loosens <current>"``; a tier, capture or review
    level the body lacks, a quantity in another unit than the pack's, or a
    ``sanctions_review`` left above ``sanctions_alert`` raises
    ``ValidationFailedError``.
    """
    _validate_amounts(override, body)
    _validate_periods(override, body)
    if override.bands is not None:
        _validate_bands(override.bands, body.screening.bands)
    if override.ubo_ownership_threshold_pct is not None:
        _check(
            "ubo.ownership_threshold_pct",
            override.ubo_ownership_threshold_pct,
            body.ubo.ownership_threshold_pct.value,
            holds=operator.le,
        )
    switches = (
        (
            "governance.committee_required",
            override.committee_required,
            body.governance.committee_required,
        ),
        (
            "kyc.geolocation_required",
            override.geolocation_required,
            body.kyc.geolocation_required,
        ),
    )
    for path, new, current in switches:
        if new is not None:
            _check(path, new, current.value, holds=_switch)


class _Applier:
    """Wraps override values with the override's provenance."""

    def __init__(self, version: int, approved_on: date) -> None:
        self.basis: str = f"tenant override v{version}"
        self.approved_on: date = approved_on

    def wrap[T](self, kind: type[Provenanced[T]], value: T) -> Provenanced[T]:
        # ``kind`` is the parametrised class (``Provenanced[Quantity]``) the field
        # declares, so the value serialises as its field expects.
        return kind(
            value=value,
            legal_basis=self.basis,
            confidence=Confidence.VERIFIED,
            verified_as_of=self.approved_on,
        )

    def pick[T](
        self,
        current: Provenanced[T],
        new: T | None,
        holds: _Holds[T],
    ) -> Provenanced[T]:
        if new is None or not holds(new, current.value):
            return current
        return self.wrap(type(current), new)

    def tier(self, tier: KycTier, override: TierLimitsOverride | None) -> KycTier:
        if override is None:
            return tier
        update: dict[str, object] = {}
        for name, new, current in _limit_pairs(override, tier.limits):
            if new is None:
                continue
            if current is None or _amount_holds(new, current.value):
                update[name] = self.wrap(Provenanced[Quantity], new)
        limits: TierLimits = tier.limits.model_copy(update=update)
        return tier.model_copy(update={"limits": limits})

    def capture(self, capture: Capture, new: Quantity | None) -> Capture:
        threshold = self.pick(capture.threshold, new, _amount_holds)
        return capture.model_copy(update={"threshold": threshold})


def apply_override(
    body: PolicyBody, override: TenantOverride, *, version: int, approved_on: date
) -> PolicyBody:
    """Lay ``override`` over ``body`` without ever loosening it.

    An entry whose tier, capture or review level the body lacks is skipped, and a
    value the body already holds more strictly (a pack that came into force after
    the override) keeps the body's value and provenance. Added lists go to the end
    of their list, skipping names already present.

    Returns:
        The body with the override applied.

    """
    apply = _Applier(version, approved_on)
    tiers = [apply.tier(t, override.tier_limits.get(t.code)) for t in body.kyc.tiers]
    review = {
        level: apply.pick(period, override.review_frequency.get(level), _shorter)
        for level, period in body.kyc.review_frequency.items()
    }
    kyc = body.kyc.model_copy(
        update={
            "tiers": tiers,
            "review_frequency": review,
            "geolocation_required": apply.pick(
                body.kyc.geolocation_required, override.geolocation_required, _switch
            ),
        }
    )
    captures = [
        apply.capture(c, override.capture_thresholds.get(c.code)) for c in body.captures
    ]
    retention = body.retention
    if override.retention is not None:
        retention = retention.model_copy(
            update={
                name: apply.pick(current, new, _longer)
                for name, new, current in _retention_pairs(
                    override.retention, retention
                )
            }
        )
    screening = body.screening
    present = {entry.value for entry in screening.mandatory_lists}
    mandatory = list(screening.mandatory_lists)
    mandatory.extend(
        apply.wrap(Provenanced[str], name)
        for name in dict.fromkeys(override.mandatory_lists_add)
        if name not in present
    )
    recommended = list(screening.recommended_lists)
    recommended.extend(
        name
        for name in dict.fromkeys(override.recommended_lists_add)
        if name not in screening.recommended_lists
    )
    bands = screening.bands
    if override.bands is not None:
        bands = bands.model_copy(
            update={
                name: value
                for name, value, current, holds in _band_rows(override.bands, bands)
                if value is not None and holds(value, current)
            }
        )
        # An override's sanctions_alert, laid over a pack that came into force
        # later with a higher review band, would leave review above alert; the
        # review band then drops to the alert band, which only tightens it.
        if bands.sanctions_review > bands.sanctions_alert:
            bands = bands.model_copy(update={"sanctions_review": bands.sanctions_alert})
    screening = screening.model_copy(
        update={
            "mandatory_lists": mandatory,
            "recommended_lists": recommended,
            "bands": bands,
        }
    )
    ubo = body.ubo.model_copy(
        update={
            "ownership_threshold_pct": apply.pick(
                body.ubo.ownership_threshold_pct,
                override.ubo_ownership_threshold_pct,
                operator.le,
            )
        }
    )
    governance = body.governance.model_copy(
        update={
            "committee_required": apply.pick(
                body.governance.committee_required,
                override.committee_required,
                _switch,
            )
        }
    )
    return body.model_copy(
        update={
            "kyc": kyc,
            "ubo": ubo,
            "screening": screening,
            "captures": captures,
            "retention": retention,
            "governance": governance,
        }
    )
