"""An amount in one of the units a pack threshold may be written in.

Amounts are normalised when they are read, so ``750``, ``"750"``, ``750.0`` and
``"750.00"`` are one value with one JSON spelling (``"750"``) and a pack hashes the
same whichever way its file writes them.
"""

from decimal import Decimal
from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def normalize_decimal(value: Decimal) -> Decimal:
    """Drop trailing zeros without switching to an exponent.

    Returns:
        ``value.normalize()``, re-quantized to a whole number when normalising
        moved the exponent above zero (``1000`` stays ``"1000"``, not ``"1E+3"``).

    """
    normal = value.normalize()
    exponent = normal.as_tuple().exponent
    if isinstance(exponent, int) and exponent > 0:
        return normal.quantize(Decimal(1))
    return normal


type PlainDecimal = Annotated[Decimal, AfterValidator(normalize_decimal)]


class Quantity(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    amount: Annotated[PlainDecimal, Field(gt=0)]
    unit: Literal["UDI", "UMA", "USD", "MXN"]
