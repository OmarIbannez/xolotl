"""The merge rules that fold several packs into their strictest body (A7).

Packs come ordered by pack code; where two packs write one value the stricter wins
and keeps its provenance, and on a tie the first pack's value stays.
"""

from collections.abc import Callable, Sequence
from decimal import Decimal
from functools import reduce

from xolotl.platform.errors import DomainError
from xolotl.platform.policy.durations import iso_duration_days
from xolotl.platform.policy.schema import (
    Capture,
    GovernancePolicy,
    KycPolicy,
    KycTier,
    PackConfig,
    PepPolicy,
    PolicyBody,
    Provenanced,
    RetentionPolicy,
    ScreeningBands,
    ScreeningPolicy,
    TierLimits,
    TrueMatchAction,
    UboPolicy,
    UnitsPolicy,
)
from xolotl.platform.units.quantity import Quantity


def _body(config: PolicyBody) -> PolicyBody:
    return PolicyBody(
        units=config.units,
        kyc=config.kyc,
        ubo=config.ubo,
        screening=config.screening,
        captures=config.captures,
        reports=config.reports,
        retention=config.retention,
        governance=config.governance,
    )


def _first_true(a: Provenanced[bool], b: Provenanced[bool]) -> Provenanced[bool]:
    return b if b.value and not a.value else a


def _lower_pct(
    a: Provenanced[Decimal], b: Provenanced[Decimal]
) -> Provenanced[Decimal]:
    return b if b.value < a.value else a


def _lower_quantity(
    a: Provenanced[Quantity], b: Provenanced[Quantity], what: str
) -> Provenanced[Quantity]:
    if a.value.unit != b.value.unit:
        msg = f"{what}: the packs disagree on the unit ({a.value.unit}, {b.value.unit})"
        raise DomainError(msg)
    return b if b.value.amount < a.value.amount else a


def _union[T](
    a: list[T], b: list[T], key: Callable[[T], str], same: Callable[[T, T], T]
) -> list[T]:
    merged = {key(item): item for item in a}
    for item in b:
        code = key(item)
        merged[code] = same(merged[code], item) if code in merged else item
    return list(merged.values())


def _limit(
    a: Provenanced[Quantity] | None, b: Provenanced[Quantity] | None, what: str
) -> Provenanced[Quantity] | None:
    # None is unlimited, so any limit is lower.
    if a is None or b is None:
        return a or b
    return _lower_quantity(a, b, what)


def _tier(a: KycTier, b: KycTier) -> KycTier:
    path = f"kyc.tiers[{a.code}].limits"
    limits = TierLimits(
        monthly_ops=_limit(a.limits.monthly_ops, b.limits.monthly_ops, path),
        max_balance=_limit(a.limits.max_balance, b.limits.max_balance, path),
    )
    return a.model_copy(update={"limits": limits})


def _dict_union[T](
    a: dict[str, T], b: dict[str, T], same: Callable[[T, T], T]
) -> dict[str, T]:
    merged = dict(a)
    for key, item in b.items():
        merged[key] = same(merged[key], item) if key in merged else item
    return merged


def _shorter(a: Provenanced[str], b: Provenanced[str]) -> Provenanced[str]:
    return b if iso_duration_days(b.value) < iso_duration_days(a.value) else a


def _longer(a: Provenanced[str], b: Provenanced[str]) -> Provenanced[str]:
    return b if iso_duration_days(b.value) > iso_duration_days(a.value) else a


def _cooling_off(
    a: Provenanced[str | None], b: Provenanced[str | None]
) -> Provenanced[str | None]:
    # The cooling-off is how long a former PEP is still treated as one, so the
    # longer period is the stricter: no cooling-off (a PEP for ever) beats any
    # period, otherwise the longer wins. The plan's merge rules say the shorter
    # wins; spec §5.3 (merge strictest) overrides them.
    if a.value is None:
        return a
    if b.value is None:
        return b
    return b if iso_duration_days(b.value) > iso_duration_days(a.value) else a


def _true_match(a: TrueMatchAction, b: TrueMatchAction) -> TrueMatchAction:
    suspend = "SUSPEND_IMMEDIATELY"
    return TrueMatchAction(
        action=suspend if suspend in {a.action, b.action} else "REVIEW",
        report=a.report if a.report is not None else b.report,
        notify_client=a.notify_client and b.notify_client,
    )


def _bands(a: ScreeningBands, b: ScreeningBands) -> ScreeningBands:
    return ScreeningBands(
        sanctions_alert=min(a.sanctions_alert, b.sanctions_alert),
        sanctions_review=min(a.sanctions_review, b.sanctions_review),
        pep_alert=min(a.pep_alert, b.pep_alert),
        id_match_floor=max(a.id_match_floor, b.id_match_floor),
        id_conflict_cap=max(a.id_conflict_cap, b.id_conflict_cap),
    )


def _capture(a: Capture, b: Capture) -> Capture:
    threshold = _lower_quantity(a.threshold, b.threshold, f"captures[{a.code}]")
    return a.model_copy(update={"threshold": threshold})


def _screening(a: ScreeningPolicy, b: ScreeningPolicy) -> ScreeningPolicy:
    return ScreeningPolicy(
        mandatory_lists=_union(
            a.mandatory_lists, b.mandatory_lists, lambda p: p.value, lambda x, _: x
        ),
        recommended_lists=sorted({*a.recommended_lists, *b.recommended_lists}),
        bands=_bands(a.bands, b.bands),
        pep=PepPolicy(
            cooling_off=_dict_union(a.pep.cooling_off, b.pep.cooling_off, _cooling_off)
        ),
        on_true_match=_dict_union(a.on_true_match, b.on_true_match, _true_match),
    )


def _merge_two(a: PolicyBody, b: PolicyBody) -> PolicyBody:
    return PolicyBody(
        units=UnitsPolicy(
            tier_limit_rule=a.units.tier_limit_rule or b.units.tier_limit_rule
        ),
        kyc=KycPolicy(
            tiers=_union(a.kyc.tiers, b.kyc.tiers, lambda t: t.code, _tier),
            review_frequency=_dict_union(
                a.kyc.review_frequency, b.kyc.review_frequency, _shorter
            ),
            geolocation_required=_first_true(
                a.kyc.geolocation_required, b.kyc.geolocation_required
            ),
        ),
        ubo=UboPolicy(
            ownership_threshold_pct=_lower_pct(
                a.ubo.ownership_threshold_pct, b.ubo.ownership_threshold_pct
            ),
            control_tests=sorted({*a.ubo.control_tests, *b.ubo.control_tests}),
            fallback=a.ubo.fallback,
            legal_person_can_be_ubo=(
                a.ubo.legal_person_can_be_ubo and b.ubo.legal_person_can_be_ubo
            ),
        ),
        screening=_screening(a.screening, b.screening),
        captures=_union(a.captures, b.captures, lambda c: c.code, _capture),
        reports=_union(a.reports, b.reports, lambda r: r.code, lambda x, _: x),
        retention=RetentionPolicy(
            kyc_after_relationship=_longer(
                a.retention.kyc_after_relationship, b.retention.kyc_after_relationship
            ),
            transactions=_longer(a.retention.transactions, b.retention.transactions),
        ),
        governance=GovernancePolicy(
            committee_required=_first_true(
                a.governance.committee_required, b.governance.committee_required
            ),
            four_eyes_on_reports=(
                a.governance.four_eyes_on_reports or b.governance.four_eyes_on_reports
            ),
        ),
    )


def merge_packs(configs: Sequence[PackConfig]) -> PolicyBody:
    """Fold packs, ordered by pack code, into the strictest of each value.

    Returns:
        The merged body; a single pack's body unchanged.

    Two packs that write one tier limit or capture threshold in different units
    raise ``DomainError``.

    Raises:
        ValueError: When ``configs`` is empty.

    """
    if not configs:
        msg = "merge_packs needs at least one pack"
        raise ValueError(msg)
    return reduce(
        _merge_two, (_body(config) for config in configs[1:]), _body(configs[0])
    )
