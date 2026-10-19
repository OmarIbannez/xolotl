"""Banxico SIE: UDI and FIX values from Mexico's central bank (``UnitValueSource``).

Live mode calls the SIE REST API with a token (``XOLOTL_BANXICO_TOKEN``). Without a
token the source is in demo mode and reads the committed seed files, UNVERIFIED rows
included; demo mode is refused when ``XOLOTL_ENV=prod`` (spec §3), so a production
fetch job reports the missing token every day instead of filling the table from the
seeds.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from xolotl.platform.enums import Confidence
from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.units.catalogue import UnitSpec, unit_spec
from xolotl.platform.units.errors import UnitSourceAuthError, UnitSourceError
from xolotl.platform.units.sources import SeedFileSource, UnitValue

BASE_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1"
MAX_RANGE_DAYS = 366
TIMEOUT_SECONDS = 10.0
_REJECTED_TOKEN = frozenset({401, 403})


# The wire format. ``datos`` is missing when a series has no data in the range.
class _Dato(BaseModel):
    fecha: str
    dato: str


class _Serie(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    id_serie: str = Field(alias="idSerie")
    datos: list[_Dato] = Field(default_factory=list)


class _Bmx(BaseModel):
    series: list[_Serie]


class _Response(BaseModel):
    bmx: _Bmx


def parse_series(payload: bytes) -> dict[str, list[tuple[date, Decimal]]]:
    """Read an SIE ``datos`` response.

    Banxico writes ``dd/mm/yyyy`` dates, thousands separators and ``N/E`` for a
    missing value; a row whose date or value does not parse is skipped.

    Returns:
        The observations of each series by its id, in the response's order.

    Raises:
        UnitSourceError: When the body does not have the SIE shape.

    """
    try:
        series = _Response.model_validate_json(payload).bmx.series
    except ValidationError as exc:
        msg = "Banxico returned a response in an unexpected format"
        raise UnitSourceError(msg) from exc
    out: dict[str, list[tuple[date, Decimal]]] = {}
    for serie in series:
        rows: list[tuple[date, Decimal]] = []
        for dato in serie.datos:
            try:
                value = Decimal(dato.dato.replace(",", ""))
                dd, mm, yyyy = (int(part) for part in dato.fecha.split("/"))
                day = date(yyyy, mm, dd)
            except (InvalidOperation, ValueError):
                continue
            rows.append((day, value))
        out[serie.id_serie] = rows
    return out


class BanxicoSieSource:
    """The Banxico SIE API as a ``UnitValueSource``, or the seed files in demo mode."""

    def __init__(
        self,
        token: SecretStr | None,
        *,
        seed_dir: Path,
        env: str,
        client: httpx.Client | None = None,
    ) -> None:
        """Call the API with ``token``, or read ``seed_dir`` when there is none."""
        self._token: SecretStr | None = token
        self._env: str = env
        self._client: httpx.Client | None = client
        self._demo: SeedFileSource = SeedFileSource(seed_dir, allow_unverified=True)

    @property
    def mode(self) -> Literal["live", "demo"]:
        """``live`` when a token is set, else ``demo``."""
        return "live" if self._token is not None else "demo"

    def _demo_source(self) -> SeedFileSource:
        if self._env == "prod":
            msg = "no XOLOTL_BANXICO_TOKEN; demo mode is refused when XOLOTL_ENV=prod"
            raise UnitSourceError(msg)
        return self._demo

    @staticmethod
    def _series(unit_code: str) -> tuple[UnitSpec, str]:
        spec = unit_spec(unit_code)
        if spec.series_id is None:
            msg = f"{unit_code} has no Banxico series"
            raise ValidationFailedError(msg)
        return spec, spec.series_id

    def _get(self, token: SecretStr, series_id: str, window: str) -> bytes:
        url = f"{BASE_URL}/series/{series_id}/datos/{window}"
        headers = {"Bmx-Token": token.get_secret_value()}
        try:
            if self._client is not None:
                response = self._client.get(url, headers=headers)
            else:
                with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
                    response = client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            msg = "Banxico SIE is unreachable, try again shortly"
            raise UnitSourceError(msg) from exc
        if response.status_code in _REJECTED_TOKEN:
            msg = "Banxico rejected the API token (check XOLOTL_BANXICO_TOKEN)"
            raise UnitSourceAuthError(msg)
        if response.is_error:
            msg = f"Banxico SIE returned HTTP {response.status_code}"
            raise UnitSourceError(msg)
        return response.content

    @staticmethod
    def _values(spec: UnitSpec, series_id: str, payload: bytes) -> list[UnitValue]:
        return [
            UnitValue(
                unit_code=spec.code,
                valid_from=day,
                valid_to=day,
                value=value,
                quote_ccy=spec.quote_ccy,
                source=f"BANXICO_SIE:{series_id}",
                confidence=Confidence.VERIFIED,
            )
            for day, value in parse_series(payload).get(series_id, [])
        ]

    def fetch(self, unit_code: str, start: date, end: date) -> list[UnitValue]:
        """Read the unit's values in ``[start, end]``, 366 days per API call.

        A failing API, a rejected token or demo mode in prod raise
        ``UnitSourceError``; a unit with no series raises ``ValidationFailedError``.

        Returns:
            One row per published day, ascending.

        Raises:
            ValidationFailedError: When ``start > end``.

        """
        if self._token is None:
            return self._demo_source().fetch(unit_code, start, end)
        spec, series_id = self._series(unit_code)
        if start > end:
            msg = "start must be on or before end"
            raise ValidationFailedError(msg)
        rows: list[UnitValue] = []
        window_start = start
        while window_start <= end:
            window_end = min(end, window_start + timedelta(days=MAX_RANGE_DAYS - 1))
            window = f"{window_start.isoformat()}/{window_end.isoformat()}"
            rows += self._values(
                spec, series_id, self._get(self._token, series_id, window)
            )
            window_start = window_end + timedelta(days=1)
        return sorted(rows, key=lambda row: row.valid_from)

    def latest_available(self, unit_code: str) -> UnitValue | None:
        """Read the unit's latest published value (``datos/oportuno``).

        Fails as ``fetch`` does.

        Returns:
            That row, or ``None`` when the series has none.

        """
        if self._token is None:
            return self._demo_source().latest_available(unit_code)
        spec, series_id = self._series(unit_code)
        rows = self._values(
            spec, series_id, self._get(self._token, series_id, "oportuno")
        )
        return rows[-1] if rows else None
