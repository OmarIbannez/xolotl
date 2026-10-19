"""The shared ``holiday`` table (spec §4.1): non-working days per country and scope."""

from datetime import date

from sqlalchemy import Date, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from xolotl.platform.db import Base
from xolotl.platform.enums import HolidayScope
from xolotl.platform.sqltypes import enum_column


class Holiday(Base):
    __tablename__: str = "holiday"

    country_code: Mapped[str] = mapped_column(String(2), primary_key=True)
    scope: Mapped[HolidayScope] = mapped_column(
        enum_column(HolidayScope), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
