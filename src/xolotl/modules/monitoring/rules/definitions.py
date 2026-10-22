"""Typed rule definitions: one Pydantic document per rule, no expression (§9.1).

A definition names its kind, the transactions that can fire it (``on``), the
aggregates it declares, its parameters with the bounds a tenant may move them
within, and how hits are deduplicated and evidenced. ``definition_hash`` is the
SHA-256 of its canonical JSON, so a changed parameter is a changed definition.

List names (``$FATF_GREY``) may appear in the country and region sets of ``on``,
``evidence.transactions_where`` and every declaration's ``where``;
``resolve_definition`` rewrites them to literal codes when a rule set is loaded
(reconciliation finding 3), so 4a's ``matches`` never sees a name.

Pure: no database, no web framework (contract ``monitoring-evaluator-is-pure``).
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import fields, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    field_validator,
    model_validator,
)

from xolotl.modules.monitoring.aggregates.declarations import (
    MAX_WINDOW_HOURS,
    AggregateDeclaration,
    TxnFilter,
    window_hours,
    window_parts,
)
from xolotl.modules.monitoring.rules.render import Locale, render
from xolotl.modules.monitoring.txn.schema import KeyKind
from xolotl.platform.policy.durations import check_iso_duration, iso_duration_days
from xolotl.platform.policy.jurisdictions import (
    COUNTRY_CODE,
    REGION_CODE,
    JurisdictionLists,
)
from xolotl.platform.units.rules import ConversionRule


class RuleFamily(StrEnum):
    ALERT = "ALERT"
    CAPTURE = "CAPTURE"


class RuleKind(StrEnum):
    THRESHOLD = "THRESHOLD"
    AGGREGATE = "AGGREGATE"
    STRUCTURING = "STRUCTURING"
    VELOCITY = "VELOCITY"
    HIGH_RISK_JURISDICTION = "HIGH_RISK_JURISDICTION"
    PROFILE_DEVIATION = "PROFILE_DEVIATION"
    TIER_LIMIT = "TIER_LIMIT"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class HitOutcome(StrEnum):
    ALERT = "ALERT"
    BLOCK = "BLOCK"
    REVIEW = "REVIEW"


type ParamValue = Decimal | int | str | bool

# The calendar month in America/Mexico_City, the one window that is not a duration.
MONTH = "MONTH"
_TIER_OUTCOMES = frozenset({HitOutcome.BLOCK.value, HitOutcome.REVIEW.value})
# A VELOCITY window is short (spec §9.1): one hour to seven days.
_VELOCITY_HOURS = (1, 7 * 24)
# The declarations a STRUCTURING rule counts over its window (plan 4b B2).
_STRUCTURING_AGGREGATES = frozenset({"near_count", "near_sum"})
# "<unit>/<ConversionRule>", as "USD/PRIOR_BANKING_DAY".
_UNIT_KEY = re.compile(r"([A-Z]{3})/([A-Z_]+)")


def _below(value: ParamValue, bound: ParamValue) -> bool:
    numeric = (Decimal, int)
    if isinstance(value, bool) or isinstance(bound, bool):
        return False
    if isinstance(value, numeric) and isinstance(bound, numeric):
        return value < bound
    if isinstance(value, str) and isinstance(bound, str):
        return value < bound
    return False


class Param[T: ParamValue](BaseModel):
    """A parameter: its value, the template's bounds and the tighten direction.

    ``tighten`` says which way a tenant may move the value (captures: ``LOWER``
    only); ``unit`` names the unit an amount is read in (``USD``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    value: T
    min: T | None = None
    max: T | None = None
    choices: list[T] | None = None
    tighten: Literal["LOWER", "RAISE"] | None = None
    unit: str | None = None

    def problem(self) -> str | None:
        """Say why the value lies outside its own bounds or choices.

        Returns:
            The reason, or None when the value is allowed.

        """
        if self.choices is not None and self.value not in self.choices:
            return f"{self.value} is not one of {self.choices}"
        if self.min is not None and _below(self.value, self.min):
            return f"{self.value} is below the minimum {self.min}"
        if self.max is not None and _below(self.max, self.value):
            return f"{self.value} is above the maximum {self.max}"
        return None


type AnyParam = Param[Decimal] | Param[int] | Param[str] | Param[bool]


def _float_at(raw: object, path: str) -> str | None:
    if isinstance(raw, float):
        return path
    items: list[tuple[str, object]] = []
    if isinstance(raw, dict):
        items = [
            (f"{path}.{key}" if path else str(key), item)
            for key, item in cast("dict[object, object]", raw).items()
        ]
    elif isinstance(raw, list):
        items = [
            (f"{path}[{index}]", item)
            for index, item in enumerate(cast("list[object]", raw))
        ]
    for where, item in items:
        found = _float_at(item, where)
        if found is not None:
            return found
    return None


def _as_decimal(text: str) -> Decimal | None:
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _param(raw: object) -> object:
    # Anything but a mapping is left for pydantic to refuse against the union.
    if not isinstance(raw, dict):
        return raw
    data = cast("dict[str, object]", raw)
    value = data.get("value")
    if isinstance(value, bool):
        return Param[bool].model_validate(data)
    if isinstance(value, int):
        return Param[int].model_validate(data)
    if isinstance(value, Decimal) or (
        isinstance(value, str) and _as_decimal(value) is not None
    ):
        return Param[Decimal].model_validate(data)
    return Param[str].model_validate(data)


def _window(text: str) -> str:
    return text if text == MONTH else check_iso_duration(text)


class Dedup(BaseModel):
    """How firings of one key collapse into one hit.

    ``window`` is an ISO duration (the bucket is ``occurred_at`` floored to it) or
    ``MONTH`` (the calendar month in America/Mexico_City); ``cooldown`` suppresses
    new hits for the key (``P0D`` for none).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    key: KeyKind = KeyKind.RELATIONSHIP
    window: str = "P1D"
    cooldown: str = "P7D"

    @field_validator("window")
    @classmethod
    def _check_window(cls, value: str) -> str:
        window = _window(value)
        # A zero window has no bucket to collapse firings into (B7 review I3).
        if window != MONTH and iso_duration_days(window) <= 0:
            msg = f"a dedup window must be longer than zero, not {value!r}"
            raise ValueError(msg)
        return window

    @field_validator("cooldown")
    @classmethod
    def _check_cooldown(cls, value: str) -> str:
        return check_iso_duration(value)


def _sorted_codes(value: object) -> JsonValue:
    if isinstance(value, frozenset):
        spelled: list[JsonValue] = [str(member) for member in value]
        return sorted(spelled, key=str)
    if isinstance(value, Decimal):
        return str(value)
    return cast("JsonValue", value)


def _hashed(value: object) -> JsonValue:
    # Decimals as A7's _canonical_value writes them, so "5000" and "5000.00" hash
    # alike; the stored JSON keeps the spelling as written.
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, frozenset | set):
        members = cast("frozenset[object]", value)
        return sorted((str(member) for member in members), key=str)
    if isinstance(value, dict):
        items = cast("dict[object, object]", value).items()
        return {str(key): _hashed(item) for key, item in items}
    if isinstance(value, list | tuple):
        return [_hashed(item) for item in cast("list[object]", value)]
    return cast("JsonValue", value)


def _filter_json(where: TxnFilter) -> dict[str, JsonValue]:
    return {
        f.name: _sorted_codes(cast("object", getattr(where, f.name)))
        for f in fields(where)
    }


def _declaration_json(declaration: AggregateDeclaration) -> dict[str, JsonValue]:
    canonical = cast("dict[str, JsonValue]", json.loads(declaration.canonical_json()))
    return {"name": declaration.name, **canonical}


# Sets serialise as sorted lists, so the JSON (and the hash) is one spelling.
type Filter = Annotated[TxnFilter, PlainSerializer(_filter_json, when_used="json")]
type Declaration = Annotated[
    AggregateDeclaration, PlainSerializer(_declaration_json, when_used="json")
]


class Evidence(BaseModel):
    """What a hit links and records: the window's matching transactions and values."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    transactions_window: str | None = None
    transactions_where: Filter = Field(default_factory=TxnFilter)
    aggregates: list[str] = []

    @field_validator("transactions_window")
    @classmethod
    def _check_window(cls, value: str | None) -> str | None:
        return None if value is None else _window(value)


class Condition(BaseModel):
    """One condition of an ``AGGREGATE`` rule.

    Either ``threshold`` (a parameter name) or ``ratio_of`` with ``ratio`` (the
    aggregate against ``ratio`` times another declared aggregate).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    aggregate: str
    op: Literal["GTE", "GT", "LTE", "LT"]
    threshold: str | None = None
    ratio_of: str | None = None
    ratio: Decimal | None = None

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        ratio_form = self.ratio_of is not None or self.ratio is not None
        complete_ratio = self.ratio_of is not None and self.ratio is not None
        if (self.threshold is not None) == ratio_form or (
            ratio_form and not complete_ratio
        ):
            msg = (
                f"condition on {self.aggregate!r}: give either threshold or "
                "ratio_of with ratio"
            )
            raise ValueError(msg)
        return self


class SegmentFilter(BaseModel):
    """The customers a rule applies to; an empty list means any."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    risk_rating: list[str] = []
    tier: list[str] = []
    monitoring_profile: list[str] = []


REQUIRED_PARAMS: Mapping[RuleKind, frozenset[str]] = {
    RuleKind.THRESHOLD: frozenset({"threshold"}),
    RuleKind.AGGREGATE: frozenset(),
    RuleKind.STRUCTURING: frozenset({"threshold", "near_ratio", "window", "min_count"}),
    RuleKind.VELOCITY: frozenset({"window"}),
    RuleKind.HIGH_RISK_JURISDICTION: frozenset({"threshold", "lists"}),
    RuleKind.PROFILE_DEVIATION: frozenset({"ratio", "history_ratio"}),
    RuleKind.TIER_LIMIT: frozenset({"outcome"}),
}
# The type a parameter of a known name holds, whichever kind declares it.
_PARAM_TYPES: Mapping[str, type] = {
    "threshold": Decimal,
    "near_ratio": Decimal,
    "window": str,
    "min_count": int,
    "count": int,
    "sum": Decimal,
    "lists": str,
    "ratio": Decimal,
    "history_ratio": Decimal,
    "outcome": str,
    "new_account_days": int,
    "dormant_days": int,
    "party_screening_status": str,
    "pep_status_not": str,
}
# A screening or PEP gate lets a THRESHOLD rule fire on any amount (plan 4b B3).
_GATES = ("party_screening_status", "pep_status_not")


class RuleDefinition(BaseModel):
    """A rule as a typed document (``language: KIND/1``, spec Decision 1)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    language: Literal["KIND/1"] = "KIND/1"
    code: str
    family: RuleFamily
    kind: RuleKind
    title: str
    typology: list[str]
    on: Filter = Field(default_factory=TxnFilter)
    key_kind: KeyKind = KeyKind.RELATIONSHIP
    segment: SegmentFilter | None = None
    # The "<unit>/<rule>" the amount parameters are read in.
    unit_key: str | None = None
    params: dict[str, AnyParam]
    aggregates: list[Declaration] = []
    conditions: list[Condition] = []
    severity: Severity
    sync: bool = False
    dedup: Dedup = Dedup()
    evidence: Evidence = Evidence()

    @model_validator(mode="before")
    @classmethod
    def _no_floats(cls, value: object) -> object:
        found = _float_at(value, "")
        if found is not None:
            msg = f"{found}: write a decimal as a string, never a float"
            raise ValueError(msg)
        return value

    @field_validator("unit_key")
    @classmethod
    def _check_unit_key(cls, value: str | None) -> str | None:
        if value is not None:
            _check_unit_key(value)
        return value

    @field_validator("params", mode="before")
    @classmethod
    def _typed_params(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        raw = cast("dict[object, object]", value)
        return {str(name): _param(item) for name, item in raw.items()}

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problem = (
            _params_problem(self)
            or _aggregates_problem(self)
            or _tier_limit_problem(self)
        )
        if problem is not None:
            msg = f"rule {self.code!r}: {problem}"
            raise ValueError(msg)
        return self

    def definition_hash(self) -> bytes:
        """Hash the canonical JSON of the definition.

        Keys are sorted, sets become sorted lists and decimals are normalised
        (``"5000"`` and ``"5000.00"`` hash alike, as in A7's ``canonical_json``).
        The bytes are those of ``xolotl.platform.audit.chain.canonical_json``,
        which a test pins.

        Returns:
            The 32-byte SHA-256.

        """
        dumped = _hashed(self.model_dump(mode="python"))
        return hashlib.sha256(canonical_json(dumped)).digest()

    def render(self, locale: Locale = "es-MX") -> str:
        """Write the analyst-readable sentence of the rule.

        Returns:
            One sentence with the parameters substituted.

        """
        return render(self, locale)

    def with_params(self, values: Mapping[str, ParamValue]) -> "RuleDefinition":
        """Copy the definition with new parameter values, keeping their bounds.

        An integer given for a decimal parameter becomes a decimal.

        Returns:
            The validated copy.

        Raises:
            ValueError: When a name is not a parameter of the definition (a value
                outside its bounds raises pydantic's ``ValidationError``, also a
                ``ValueError``).

        """
        unknown = sorted(set(values) - set(self.params))
        if unknown:
            msg = f"rule {self.code!r} has no parameter {', '.join(unknown)}"
            raise ValueError(msg)
        params: dict[str, object] = {}
        for name, param in self.params.items():
            data = param.model_dump()
            if name in values:
                data["value"] = _coerced(param, values[name])
            params[name] = data
        return RuleDefinition.model_validate({**self.model_dump(), "params": params})


def _coerced(param: AnyParam, value: ParamValue) -> ParamValue:
    if (
        isinstance(param.value, Decimal)
        and isinstance(value, int)
        and not isinstance(value, bool)
    ):
        return Decimal(value)
    return value


def canonical_json(value: object) -> bytes:
    """Spell ``value`` as compact JSON with sorted keys, non-ASCII kept.

    A copy of ``xolotl.platform.audit.chain.canonical_json``, kept here because
    that module reads the database and this one is pure; the test
    ``test_the_hash_input_is_the_audit_chains_canonical_json`` pins the two equal.

    Returns:
        The UTF-8 bytes.

    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _required_problem(definition: RuleDefinition) -> str | None:
    required = set(REQUIRED_PARAMS[definition.kind])
    params = definition.params
    gated = definition.on.counterparty_screening_status or any(
        gate in params for gate in _GATES
    )
    if definition.kind is RuleKind.THRESHOLD and gated:
        required.discard("threshold")
    missing = sorted(required - set(params))
    if missing:
        return f"a {definition.kind} rule needs the parameters {', '.join(missing)}"
    if definition.kind is RuleKind.VELOCITY and not {"count", "sum"} & set(params):
        return "a VELOCITY rule needs the parameter count or sum"
    if definition.kind is RuleKind.AGGREGATE and not definition.conditions:
        return "an AGGREGATE rule needs at least one condition"
    return None


def _check_unit_key(text: str) -> None:
    match = _UNIT_KEY.fullmatch(text)
    if match is None or match.group(2) not in ConversionRule:
        rules = ", ".join(ConversionRule)
        msg = f"unit_key {text!r} must read <unit>/<rule> with a rule of {rules}"
        raise ValueError(msg)


def _window_problem(definition: RuleDefinition) -> str | None:
    # A7's DURATION grammar and bounds (spec §8.2: PT1H..P6M, months not mixed
    # with days or hours), since the window feeds the rule's declarations.
    param = definition.params.get("window")
    if param is None or not isinstance(param.value, str):
        return None
    window = param.value
    hours = window_hours(window)
    if hours is None or not 1 <= hours <= MAX_WINDOW_HOURS:
        return f"parameter 'window' runs from PT1H to P6M, not {window!r}"
    months, days, part_hours = window_parts(window) or (0, 0, 0)
    if months and (days or part_hours):
        return f"parameter 'window' counts months or days, not {window!r}"
    shortest, longest = _VELOCITY_HOURS
    if definition.kind is RuleKind.VELOCITY and not shortest <= hours <= longest:
        return f"a VELOCITY window lies within PT1H..P7D, not {window}"
    if definition.kind is RuleKind.STRUCTURING:
        for declaration in definition.aggregates:
            if declaration.name in _STRUCTURING_AGGREGATES and (
                declaration.window != window
            ):
                return (
                    f"aggregate {declaration.name!r} counts over "
                    f"{declaration.window}, not the window {window}"
                )
    return None


def _params_problem(definition: RuleDefinition) -> str | None:
    required = _required_problem(definition)
    if required is not None:
        return required
    for name, param in definition.params.items():
        expected = _PARAM_TYPES.get(name)
        if expected is not None and type(param.value) is not expected:
            return f"parameter {name!r} must be a {expected.__name__}"
        problem = param.problem()
        if problem is not None:
            return f"parameter {name!r}: {problem}"
    return _window_problem(definition)


def _aggregates_problem(definition: RuleDefinition) -> str | None:
    names = [declaration.name for declaration in definition.aggregates]
    if len(set(names)) != len(names):
        return "aggregate names must be unique"
    for declaration in definition.aggregates:
        try:
            declaration.validate()
        except ValueError as exc:
            return str(exc)
    declared = set(names)
    for condition in definition.conditions:
        for name in (condition.aggregate, condition.ratio_of):
            if name is not None and name not in declared:
                return f"condition reads the undeclared aggregate {name!r}"
        if condition.threshold is not None and (
            condition.threshold not in definition.params
        ):
            return f"condition names the unknown parameter {condition.threshold!r}"
    unknown = sorted(set(definition.evidence.aggregates) - declared)
    if unknown:
        return f"evidence names undeclared aggregates {', '.join(unknown)}"
    return None


def _tier_limit_problem(definition: RuleDefinition) -> str | None:
    if definition.kind is not RuleKind.TIER_LIMIT:
        return None
    outcome = definition.params["outcome"]
    choices = outcome.choices
    if choices is None or frozenset(map(str, choices)) != _TIER_OUTCOMES:
        return "a TIER_LIMIT outcome chooses between BLOCK and REVIEW"
    if definition.dedup.window != MONTH:
        return "a TIER_LIMIT deduplicates per calendar MONTH"
    if not definition.sync:
        return "a TIER_LIMIT is evaluated synchronously (sync: true)"
    return None


def _resolved(
    codes: frozenset[str], lists: JurisdictionLists, *, regions: bool
) -> frozenset[str]:
    names = [code for code in codes if code.startswith("$")]
    literal = codes.difference(names)
    pattern, what = (REGION_CODE, "region") if regions else (COUNTRY_CODE, "country")
    for code in sorted(literal):
        if pattern.fullmatch(code) is None:
            msg = f"{code!r} is not a {what} code (a list name is written $NAME)"
            raise ValueError(msg)
    if not names:
        return codes
    resolve = lists.resolve_regions if regions else lists.resolve_countries
    return literal | resolve(names)


def resolve_list_names(where: TxnFilter, lists: JurisdictionLists) -> TxnFilter:
    """Replace every ``$NAME`` of the country and region sets by its codes.

    Literal codes pass through; the other fields are untouched.

    A name that is not a set of ``lists`` or names an empty one (from
    ``JurisdictionLists``), or a literal that is not a country (ISO 3166-1
    alpha-2) or region (ISO 3166-2) code, raises ``ValueError``.

    Returns:
        The filter with literal codes only.

    """
    return replace(
        where,
        counterparty_country_in=_resolved(
            where.counterparty_country_in, lists, regions=False
        ),
        counterparty_country_not_in=_resolved(
            where.counterparty_country_not_in, lists, regions=False
        ),
        geo_admin_area_in=_resolved(where.geo_admin_area_in, lists, regions=True),
    )


def resolve_definition(
    definition: RuleDefinition, lists: JurisdictionLists
) -> RuleDefinition:
    """Resolve list names over ``on``, the evidence filter and every declaration.

    Every declaration also gets ``lists_hash`` (the lists file's SHA-256 in hex),
    so a changed ``_jurisdictions.yaml`` yields new ``agg_id``s (spec §8.2). The
    names of ``params.lists`` (``"FATF_BLACK,FATF_GREY"``) are checked too, so an
    unknown or empty set fails the load rather than the evaluation. Each of these
    raises ``ValueError``, as ``resolve_list_names`` does.

    Returns:
        The resolved copy.

    """
    named = definition.params.get("lists")
    if named is not None:
        _ = lists.resolve_countries(
            name.strip() for name in str(named.value).split(",")
        )
    lists_hash = lists.content_hash.hex()
    aggregates = [
        replace(
            declaration,
            where=resolve_list_names(declaration.where, lists),
            lists_hash=lists_hash,
        )
        for declaration in definition.aggregates
    ]
    evidence = definition.evidence.model_copy(
        update={
            "transactions_where": resolve_list_names(
                definition.evidence.transactions_where, lists
            )
        }
    )
    return definition.model_copy(
        update={
            "on": resolve_list_names(definition.on, lists),
            "evidence": evidence,
            "aggregates": aggregates,
        }
    )
