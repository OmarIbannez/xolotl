"""The units the platform knows, their quote currency, series and seed file."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from xolotl.platform.errors import ValidationFailedError


@dataclass(frozen=True, slots=True)
class UnitSpec:
    """One unit: daily units hold one value per day, the others a dated range."""

    code: str
    quote_ccy: str
    daily: bool
    series_id: str | None
    seed_file: str


UNITS: Mapping[str, UnitSpec] = MappingProxyType({
    "UMA": UnitSpec("UMA", "MXN", daily=False, series_id=None, seed_file="UMA.yaml"),
    "UDI": UnitSpec(
        "UDI", "MXN", daily=True, series_id="SP68257", seed_file="UDI.seed.yaml"
    ),
    "USD_MXN_FIX": UnitSpec(
        "USD_MXN_FIX",
        "MXN",
        daily=True,
        series_id="SF43718",
        seed_file="USD_MXN_FIX.seed.yaml",
    ),
})

# ``Quantity.unit`` to ``unit_code``; "MXN" is the quote currency itself.
UNIT_OF: Mapping[str, str] = MappingProxyType({
    "UDI": "UDI",
    "UMA": "UMA",
    "USD": "USD_MXN_FIX",
})


def unit_spec(unit_code: str) -> UnitSpec:
    """Look a unit up in the catalogue.

    Returns:
        Its ``UnitSpec``.

    Raises:
        ValidationFailedError: When the catalogue does not list it.

    """
    spec = UNITS.get(unit_code)
    if spec is None:
        msg = f"unknown unit {unit_code}"
        raise ValidationFailedError(msg)
    return spec
