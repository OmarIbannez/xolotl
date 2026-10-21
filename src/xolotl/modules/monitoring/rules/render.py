"""The analyst-readable sentence of a rule, in es-MX and en (spec §9.1, FR-TM-004).

One sentence per kind with the parameters substituted, so the console can show
what a version does without reading its JSON. Amounts read ``<amount> <unit>``
with a narrow no-break space (U+202F) between thousands in es-MX (``5 000 USD``)
and a comma in en (``5,000 USD``).

Pure: no database, no web framework (contract ``monitoring-evaluator-is-pure``).
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Literal, Protocol

from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    TxnFilter,
    WindowKind,
)
from xolotl.modules.monitoring.txn.schema import Direction, Instrument

type Locale = Literal["es-MX", "en"]


# What the renderer reads of a definition. ``definitions.RuleDefinition`` fits these
# structurally, so this module need not import ``definitions`` (which imports it).
class RenderedParam(Protocol):
    @property
    def value(self) -> Decimal | int | str | bool: ...
    @property
    def unit(self) -> str | None: ...


class RenderedCondition(Protocol):
    @property
    def aggregate(self) -> str: ...
    @property
    def op(self) -> str: ...
    @property
    def threshold(self) -> str | None: ...
    @property
    def ratio_of(self) -> str | None: ...
    @property
    def ratio(self) -> Decimal | None: ...


class RenderedRule(Protocol):
    @property
    def kind(self) -> str: ...
    @property
    def on(self) -> TxnFilter: ...
    @property
    def unit_key(self) -> str | None: ...
    @property
    def params(self) -> Mapping[str, RenderedParam]: ...
    @property
    def aggregates(self) -> Sequence[AggregateDeclaration]: ...
    @property
    def conditions(self) -> Sequence[RenderedCondition]: ...


_THIN_SPACE = chr(0x202F)  # U+202F NARROW NO-BREAK SPACE
_DURATION = re.compile(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(\d+)H)?$")
_DURATION_WORDS: dict[Locale, tuple[tuple[str, str], ...]] = {
    "es-MX": (("año", "años"), ("mes", "meses"), ("día", "días"), ("hora", "horas")),
    "en": (("year", "years"), ("month", "months"), ("day", "days"), ("hour", "hours")),
}
_AND: dict[Locale, str] = {"es-MX": " y ", "en": " and "}
_INSTRUMENT_ES = {
    Instrument.CASH: " en efectivo",
    Instrument.TRANSFER: " por transferencia",
    Instrument.CARD: " con tarjeta",
    Instrument.CHEQUE: " con cheque",
    Instrument.CRYPTO: " con activos virtuales",
    Instrument.FX: " de cambio de divisas",
    Instrument.FEE: " de comisión",
}
_INSTRUMENT_EN = {
    Instrument.CASH: "cash ",
    Instrument.TRANSFER: "transfer ",
    Instrument.CARD: "card ",
    Instrument.CHEQUE: "cheque ",
    Instrument.CRYPTO: "virtual-asset ",
    Instrument.FX: "foreign-exchange ",
    Instrument.FEE: "fee ",
}
_FUNCTION: dict[Locale, dict[AggregateFunction, str]] = {
    "es-MX": {
        AggregateFunction.SUM: "la suma de",
        AggregateFunction.COUNT: "el número de",
        AggregateFunction.AVG: "el promedio de",
        AggregateFunction.MAX: "el máximo de",
        AggregateFunction.MIN: "el mínimo de",
        AggregateFunction.FIRST_SEEN: "la primera de",
        AggregateFunction.LAST_SEEN: "la última de",
    },
    "en": {
        AggregateFunction.SUM: "the sum of",
        AggregateFunction.COUNT: "the number of",
        AggregateFunction.AVG: "the average of",
        AggregateFunction.MAX: "the largest of",
        AggregateFunction.MIN: "the smallest of",
        AggregateFunction.FIRST_SEEN: "the first of",
        AggregateFunction.LAST_SEEN: "the last of",
    },
}
_DISTINCT: dict[Locale, dict[str, str]] = {
    "es-MX": {
        "COUNTERPARTY": "contrapartes distintas",
        "DEVICE": "dispositivos distintos",
        "COUNTRY": "países distintos",
        "ACCOUNT": "cuentas distintas",
    },
    "en": {
        "COUNTERPARTY": "distinct counterparties",
        "DEVICE": "distinct devices",
        "COUNTRY": "distinct countries",
        "ACCOUNT": "distinct accounts",
    },
}
_OP: dict[Locale, dict[str, str]] = {
    "es-MX": {
        "GTE": "es de al menos",
        "GT": "supera",
        "LTE": "es de a lo más",
        "LT": "es menor que",
    },
    "en": {
        "GTE": "is at least",
        "GT": "exceeds",
        "LTE": "is at most",
        "LT": "is below",
    },
}
_OUTCOME: dict[Locale, dict[str, str]] = {
    "es-MX": {
        "BLOCK": "la operación se bloquea",
        "REVIEW": "la operación pasa a revisión",
        "ALERT": "se genera una alerta",
    },
    "en": {
        "BLOCK": "the operation is blocked",
        "REVIEW": "the operation goes to review",
        "ALERT": "an alert is raised",
    },
}


def _number(value: Decimal | int, locale: Locale) -> str:
    grouped = format(value, ",d") if isinstance(value, int) else format(value, ",f")
    return grouped.replace(",", _THIN_SPACE) if locale == "es-MX" else grouped


def _percent(ratio: Decimal, locale: Locale) -> str:
    number = _number((ratio * 100).normalize(), locale)
    return f"{number} %" if locale == "es-MX" else f"{number}%"


def _amount(definition: RenderedRule, param: RenderedParam, locale: Locale) -> str:
    unit = param.unit
    if unit is None and definition.unit_key is not None:
        unit = definition.unit_key.split("/", 1)[0]
    return _value(param, locale, unit)


def _value(param: RenderedParam, locale: Locale, unit: str | None) -> str:
    value = param.value
    text = (
        _number(value, locale)
        if isinstance(value, Decimal | int) and not isinstance(value, bool)
        else str(value)
    )
    return text if unit is None else f"{text} {unit}"


def _join(items: Iterable[str], locale: Locale) -> str:
    words = list(items)
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + _AND[locale] + words[-1]


def _duration(text: str, locale: Locale) -> str:
    match = _DURATION.match(text)
    if match is None:
        return text
    parts = [
        f"{count} {one if count == 1 else many}"
        for count, (one, many) in zip(
            (int(part or 0) for part in match.groups()),
            _DURATION_WORDS[locale],
            strict=True,
        )
        if count
    ]
    return _join(parts, locale)


def _within(text: str, locale: Locale) -> str:
    if text == "MONTH":
        return "en el mes calendario" if locale == "es-MX" else "in the calendar month"
    phrase = _duration(text, locale)
    return f"en {phrase}" if locale == "es-MX" else f"within {phrase}"


def _instrument(where: TxnFilter) -> Instrument | None:
    if len(where.instrument) != 1:
        return None
    return next(iter(where.instrument))


def _direction(where: TxnFilter) -> Direction | None:
    if len(where.direction) != 1:
        return None
    return next(iter(where.direction))


def _cash_noun(where: TxnFilter, locale: Locale, *, plural: bool) -> str | None:
    if _instrument(where) is not Instrument.CASH:
        return None
    nouns = {
        ("es-MX", Direction.IN): ("un depósito en efectivo", "depósitos en efectivo"),
        ("es-MX", Direction.OUT): ("un retiro en efectivo", "retiros en efectivo"),
        ("en", Direction.IN): ("a cash deposit", "cash deposits"),
        ("en", Direction.OUT): ("a cash withdrawal", "cash withdrawals"),
    }
    direction = _direction(where)
    found = None if direction is None else nouns.get((locale, direction))
    return None if found is None else found[plural]


def _plural(where: TxnFilter, locale: Locale, *, directional: bool) -> str:
    cash = _cash_noun(where, locale, plural=True)
    if cash is not None:
        return cash
    instrument = _instrument(where)
    direction = _direction(where) if directional else None
    if locale == "es-MX":
        moved = {Direction.IN: " recibidas", Direction.OUT: " enviadas"}
        how = "" if instrument is None else _INSTRUMENT_ES[instrument]
        return f"operaciones{moved.get(direction, '') if direction else ''}{how}"
    moved_en = {Direction.IN: "incoming ", Direction.OUT: "outgoing "}
    how_en = "" if instrument is None else _INSTRUMENT_EN[instrument]
    where_to = moved_en.get(direction, "") if direction else ""
    return f"{where_to}{how_en}operations"


def _singular(where: TxnFilter, locale: Locale) -> str:
    cash = _cash_noun(where, locale, plural=False)
    if cash is not None:
        return cash
    instrument = _instrument(where)
    if locale == "es-MX":
        how = "" if instrument is None else _INSTRUMENT_ES[instrument]
        return f"una operación{how}"
    noun = f"{'' if instrument is None else _INSTRUMENT_EN[instrument]}operation"
    article = "an" if noun[0] in "aeiou" else "a"
    return f"{article} {noun}"


def _verb(where: TxnFilter, locale: Locale) -> str:
    direction = _direction(where)
    if _cash_noun(where, locale, plural=True) is not None:
        direction = Direction.IN if direction is Direction.IN else None
    verbs = {
        "es-MX": {Direction.IN: "recibe", Direction.OUT: "envía"},
        "en": {Direction.IN: "receives", Direction.OUT: "sends"},
    }
    default = "realiza" if locale == "es-MX" else "makes"
    return default if direction is None else verbs[locale].get(direction, default)


def _codes(codes: frozenset[str], locale: Locale) -> str:
    return _join(sorted(code.removeprefix("$") for code in codes), locale)


def _listed(
    codes: frozenset[str], locale: Locale, words: tuple[str, str, str, str]
) -> str:
    # words: the es-MX singular and plural, then the en ones, with {} for codes.
    one, many = words[:2] if locale == "es-MX" else words[2:]
    return (one if len(codes) == 1 else many).format(_codes(codes, locale))


def _band(where: TxnFilter, locale: Locale) -> str:
    unit_key = where.amount_unit_key
    unit = None if unit_key is None else unit_key.split("/", 1)[0]

    def amount(value: Decimal) -> str:
        text = _number(value, locale)
        return text if unit is None else f"{text} {unit}"

    es = locale == "es-MX"
    low, high = where.amount_gte, where.amount_lt
    if low is not None and high is not None:
        return (
            f" de al menos {amount(low)} y menos de {amount(high)}"
            if es
            else f" of at least {amount(low)} and below {amount(high)}"
        )
    if low is not None:
        return f" de al menos {amount(low)}" if es else f" of at least {amount(low)}"
    if high is not None:
        return f" de menos de {amount(high)}" if es else f" below {amount(high)}"
    return ""


def _routes(where: TxnFilter, locale: Locale) -> str:
    clauses: list[str] = []
    if where.rail:
        rails = frozenset(map(str, where.rail))
        clauses.append(
            _listed(
                rails,
                locale,
                (
                    " por la red de pago {}",
                    " por las redes de pago {}",
                    " on the {} rail",
                    " on the {} rails",
                ),
            )
        )
    if where.channel:
        channels = frozenset(map(str, where.channel))
        clauses.append(
            _listed(
                channels,
                locale,
                (
                    " por el canal {}",
                    " por los canales {}",
                    " through the {} channel",
                    " through the {} channels",
                ),
            )
        )
    if where.currency:
        clauses.append(
            _listed(
                where.currency,
                locale,
                (" en moneda {}", " en monedas {}", " in {}", " in {}"),
            )
        )
    return "".join(clauses) + _band(where, locale)


def _qualifiers(where: TxnFilter, locale: Locale) -> str:
    es = locale == "es-MX"
    clauses: list[str] = [_routes(where, locale)]
    if where.counterparty_screening_status:
        status = _codes(where.counterparty_screening_status, locale)
        clauses.append(
            f" con una contraparte en estado {status}"
            if es
            else f" with a counterparty in status {status}"
        )
    if where.self_custody is not None:
        custody = {
            (True, True): " hacia una billetera de autocustodia",
            (True, False): " hacia una billetera custodiada",
            (False, True): " to a self-custody wallet",
            (False, False): " to a custodial wallet",
        }
        clauses.append(custody[es, where.self_custody])
    if where.cross_border is not None:
        border = {
            (True, True): " con una contraparte en el extranjero",
            (True, False): " con una contraparte nacional",
            (False, True): " with a counterparty abroad",
            (False, False): " with a domestic counterparty",
        }
        clauses.append(border[es, where.cross_border])
    if where.counterparty_country_in:
        countries = _codes(where.counterparty_country_in, locale)
        clauses.append(
            f" con contrapartes en {countries}"
            if es
            else f" with counterparties in {countries}"
        )
    if where.counterparty_country_not_in:
        countries = _codes(where.counterparty_country_not_in, locale)
        clauses.append(
            f" con contrapartes fuera de {countries}"
            if es
            else f" with counterparties outside {countries}"
        )
    if where.geo_admin_area_in:
        areas = _codes(where.geo_admin_area_in, locale)
        clauses.append(f" desde {areas}" if es else f" from {areas}")
    if where.amount_round_multiple is not None:
        multiple = _number(where.amount_round_multiple, locale)
        clauses.append(
            f" por montos múltiplos de {multiple}"
            if es
            else f" in multiples of {multiple}"
        )
    return "".join(clauses)


def _gates(definition: RenderedRule, locale: Locale) -> str:
    es = locale == "es-MX"
    clauses: list[str] = []
    status = definition.params.get("party_screening_status")
    if status is not None:
        clauses.append(
            f" mientras el cliente tiene estado de screening {status.value}"
            if es
            else f" while the customer's screening status is {status.value}"
        )
    pep = definition.params.get("pep_status_not")
    if pep is not None:
        if pep.value == "NONE":
            clauses.append(
                " cuando el cliente es PEP" if es else " when the customer is a PEP"
            )
        else:
            clauses.append(
                f" cuando el estado PEP del cliente no es {pep.value}"
                if es
                else f" when the customer's PEP status is not {pep.value}"
            )
    new_account = definition.params.get("new_account_days")
    if new_account is not None:
        days = _value(new_account, locale, None)
        clauses.append(
            f" durante sus primeros {days} días de relación"
            if es
            else f" during the first {days} days of the relationship"
        )
    dormant = definition.params.get("dormant_days")
    if dormant is not None:
        days = _value(dormant, locale, None)
        clauses.append(
            f" tras {days} días sin actividad"
            if es
            else f" after {days} days without activity"
        )
    return "".join(clauses)


def _opening(locale: Locale) -> str:
    return "Dispara cuando" if locale == "es-MX" else "Fires when"


def _subject(definition: RenderedRule, locale: Locale) -> str:
    customer = "el cliente" if locale == "es-MX" else "the customer"
    return f"{_opening(locale)} {customer} {_verb(definition.on, locale)}"


def _threshold(definition: RenderedRule, locale: Locale) -> str:
    on = definition.on
    noun = _singular(on, locale)
    threshold = definition.params.get("threshold")
    if threshold is None:
        return f"{_subject(definition, locale)} {noun}"
    amount = _amount(definition, threshold, locale)
    more = f"de {amount} o más" if locale == "es-MX" else f"of {amount} or more"
    return f"{_subject(definition, locale)} {noun} {more}"


def _sentence_threshold(definition: RenderedRule, locale: Locale) -> str:
    return (
        _threshold(definition, locale)
        + _qualifiers(definition.on, locale)
        + _gates(definition, locale)
    )


def _sentence_structuring(definition: RenderedRule, locale: Locale) -> str:
    params = definition.params
    count = _value(params["min_count"], locale, None)
    near = params["near_ratio"].value
    near_text = _percent(near, locale) if isinstance(near, Decimal) else str(near)
    noun = _plural(definition.on, locale, directional=False)
    quals = _qualifiers(definition.on, locale)
    within = _within(str(params["window"].value), locale)
    if locale == "es-MX":
        return (
            f"{_subject(definition, locale)} {count} o más {noun}{quals} entre "
            f"{near_text} y 100 % del umbral {within} y su suma alcanza el umbral"
        )
    return (
        f"{_subject(definition, locale)} {count} or more {noun}{quals} between "
        f"{near_text} and 100% of the threshold {within} and their sum reaches "
        "the threshold"
    )


def _count_or_sum(definition: RenderedRule, noun: str, locale: Locale) -> str:
    params = definition.params
    es = locale == "es-MX"
    count = params.get("count")
    total = params.get("sum")
    parts: list[str] = []
    if count is not None:
        number = _value(count, locale, None)
        parts.append(f"{number} o más {noun}" if es else f"{number} or more {noun}")
    if total is not None:
        amount = _amount(definition, total, locale)
        if count is None:
            parts.append(
                f"{noun} por un total de {amount} o más"
                if es
                else f"{noun} totalling {amount} or more"
            )
        else:
            parts.append(
                f"un total de {amount} o más" if es else f"a total of {amount} or more"
            )
        return (" o " if es else " or ").join(parts)
    return parts[0]


def _sentence_velocity(definition: RenderedRule, locale: Locale) -> str:
    noun = _plural(definition.on, locale, directional=False)
    noun += _qualifiers(definition.on, locale)
    what = _count_or_sum(definition, noun, locale)
    within = _within(str(definition.params["window"].value), locale)
    return f"{_subject(definition, locale)} {what} {within}{_gates(definition, locale)}"


def _lists(definition: RenderedRule, locale: Locale) -> str:
    names = [
        name.strip().removeprefix("$")
        for name in str(definition.params["lists"].value).split(",")
        if name.strip()
    ]
    joined = _join(names, locale)
    if locale == "es-MX":
        return f"la lista {joined}" if len(names) == 1 else f"las listas {joined}"
    return f"the list {joined}" if len(names) == 1 else f"the lists {joined}"


def _sentence_jurisdiction(definition: RenderedRule, locale: Locale) -> str:
    lists = _lists(definition, locale)
    place = (
        f" con una contraparte o ubicación en {lists}"
        if locale == "es-MX"
        else f" with a counterparty or location on {lists}"
    )
    return (
        _threshold(definition, locale)
        + _qualifiers(definition.on, locale)
        + place
        + _listed_window(definition, locale)
        + _gates(definition, locale)
    )


def _listed_window(definition: RenderedRule, locale: Locale) -> str:
    # The window branch: to_listed_count / to_listed_sum reach count / sum.
    params = definition.params
    window = params.get("window")
    if window is None or not {"count", "sum"} & set(params):
        return ""
    noun = _plural(definition.on, locale, directional=False)
    what = _count_or_sum(definition, noun, locale)
    within = _within(str(window.value), locale)
    if locale == "es-MX":
        return f" o cuando, {within}, realiza {what} con contrapartes en esas listas"
    return f", or when {within} they make {what} with counterparties on those lists"


def _window_phrase(declaration: AggregateDeclaration, locale: Locale) -> str:
    es = locale == "es-MX"
    match declaration.window_kind:
        case WindowKind.DURATION:
            return _within(declaration.window, locale)
        case WindowKind.MONTH:
            return _within("MONTH", locale)
        case WindowKind.QUARTER:
            return "en el trimestre calendario" if es else "in the calendar quarter"
        case WindowKind.ACCUM:
            months = _duration(declaration.window, locale)
            return (
                f"en los {months} desde la primera operación"
                if es
                else f"in the {months} from the first operation"
            )


def _aggregate_phrase(declaration: AggregateDeclaration, locale: Locale) -> str:
    where = declaration.where
    noun = _plural(where, locale, directional=True) + _qualifiers(where, locale)
    window = _window_phrase(declaration, locale)
    if declaration.function is AggregateFunction.DISTINCT_COUNT:
        distinct = _DISTINCT[locale][declaration.distinct_of or "COUNTERPARTY"]
        if locale == "es-MX":
            return f"el número de {distinct} en {noun} {window}"
        return f"the number of {distinct} in {noun} {window}"
    return f"{_FUNCTION[locale][declaration.function]} {noun} {window}"


def _condition(
    definition: RenderedRule, condition: RenderedCondition, locale: Locale
) -> str:
    declared = {declaration.name: declaration for declaration in definition.aggregates}
    left = _aggregate_phrase(declared[condition.aggregate], locale)
    op = _OP[locale][condition.op]
    if condition.threshold is not None:
        param = definition.params[condition.threshold]
        return f"{left} {op} {_value(param, locale, param.unit)}"
    ratio = _percent(condition.ratio or Decimal(0), locale)
    right = _aggregate_phrase(declared[condition.ratio_of or ""], locale)
    if locale == "es-MX":
        # "de el" contracts to "del" in Spanish.
        of_right = f"del {right[3:]}" if right.startswith("el ") else f"de {right}"
        return f"{left} {op} el {ratio} {of_right}"
    return f"{left} {op} {ratio} of {right}"


def _sentence_aggregate(definition: RenderedRule, locale: Locale) -> str:
    conditions = [
        _condition(definition, condition, locale) for condition in definition.conditions
    ]
    joined = _AND[locale].join(conditions)
    if definition.on == TxnFilter():
        return f"{_opening(locale)} {joined}{_gates(definition, locale)}"
    trigger = (
        _subject(definition, locale)
        + " "
        + _singular(definition.on, locale)
        + _qualifiers(definition.on, locale)
    )
    return f"{trigger}{_AND[locale]}{joined}{_gates(definition, locale)}"


def _sentence_profile(definition: RenderedRule, locale: Locale) -> str:
    params = definition.params
    ratio = _value(params["ratio"], locale, None)
    history = _value(params["history_ratio"], locale, None)
    noun = _plural(definition.on, locale, directional=True)
    noun += _qualifiers(definition.on, locale)
    if locale == "es-MX":
        return (
            f"Dispara cuando la suma del mes de {noun} del cliente es de al menos "
            f"{ratio} veces su perfil declarado o {history} veces su promedio "
            "mensual de los últimos 6 meses"
        )
    return (
        f"Fires when the customer's month-to-date sum of {noun} is at least "
        f"{ratio} times the declared profile or {history} times their monthly "
        "average over the last 6 months"
    )


def _sentence_tier_limit(definition: RenderedRule, locale: Locale) -> str:
    outcome = _OUTCOME[locale].get(str(definition.params["outcome"].value), "")
    if locale == "es-MX":
        return (
            "Dispara cuando los depósitos del cliente en el mes calendario superan "
            f"el límite mensual de su nivel y {outcome}"
        )
    return (
        "Fires when the customer's deposits in the calendar month exceed their "
        f"tier's monthly limit and {outcome}"
    )


_SENTENCES: dict[str, Callable[[RenderedRule, Locale], str]] = {
    "THRESHOLD": _sentence_threshold,
    "AGGREGATE": _sentence_aggregate,
    "STRUCTURING": _sentence_structuring,
    "VELOCITY": _sentence_velocity,
    "HIGH_RISK_JURISDICTION": _sentence_jurisdiction,
    "PROFILE_DEVIATION": _sentence_profile,
    "TIER_LIMIT": _sentence_tier_limit,
}


def render(definition: RenderedRule, locale: Locale) -> str:
    """Write the rule's sentence in ``locale``, parameters substituted.

    Returns:
        One sentence without a final full stop, as the console shows it.

    """
    return _SENTENCES[definition.kind](definition, locale)
