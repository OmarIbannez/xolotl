"""An amount in one of the units a pack threshold may be written in."""

from decimal import Decimal
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class Quantity(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    amount: Decimal = Field(gt=0)
    unit: Literal["UDI", "UMA", "USD", "MXN"]
