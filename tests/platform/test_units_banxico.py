"""The Banxico SIE source: the live API through ``respx``, demo mode over the seeds.

Live mode reads ``series/{id}/datos/{start}/{end}`` (at most 366 days a call) and
``series/{id}/datos/oportuno`` with the ``Bmx-Token`` header; every failure is a
``UnitSourceError``, a rejected token the ``UnitSourceAuthError`` subclass. Without a
token the source reads the committed seed files, outside prod only.
"""

from collections.abc import Generator
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import SecretStr

from xolotl.platform.enums import Confidence
from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.units import (
    BanxicoSieSource,
    UnitSourceAuthError,
    UnitSourceError,
    parse_series,
)
from xolotl.platform.units.banxico import BASE_URL

REPO = Path(__file__).resolve().parents[2]
SEEDS = REPO / "policy-packs/_units"
TOKEN = SecretStr("tok-1")

FIX_BODY = {
    "bmx": {
        "series": [
            {
                "idSerie": "SF43718",
                "titulo": "FIX",
                "datos": [
                    {"fecha": "10/12/2025", "dato": "18.1000"},
                    {"fecha": "11/12/2025", "dato": "18.2500"},
                    {"fecha": "12/12/2025", "dato": "N/E"},
                    {"fecha": "15/12/2025", "dato": "1,018.4000"},
                ],
            }
        ]
    }
}


@pytest.fixture
def sie() -> Generator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


def live(env: str = "dev") -> BanxicoSieSource:
    return BanxicoSieSource(TOKEN, seed_dir=SEEDS, env=env)


def test_live_fetch_parses_the_sie_shape(sie: respx.MockRouter):
    route = sie.get(f"{BASE_URL}/series/SF43718/datos/2025-12-10/2025-12-15").mock(
        return_value=httpx.Response(200, json=FIX_BODY)
    )
    source = live()
    assert source.mode == "live"
    rows = source.fetch("USD_MXN_FIX", date(2025, 12, 10), date(2025, 12, 15))
    assert [r.value for r in rows] == [
        Decimal("18.1000"),
        Decimal("18.2500"),
        Decimal("1018.4000"),
    ]
    assert [r.valid_from for r in rows] == [
        date(2025, 12, 10),
        date(2025, 12, 11),
        date(2025, 12, 15),
    ]
    assert all(r.valid_to == r.valid_from for r in rows)
    assert all(r.source == "BANXICO_SIE:SF43718" for r in rows)
    assert all(r.quote_ccy == "MXN" for r in rows)
    assert all(r.confidence is Confidence.VERIFIED for r in rows)
    assert route.calls.last.request.headers["Bmx-Token"] == "tok-1"


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_token_is_a_source_error(sie: respx.MockRouter, status: int):
    _ = sie.get(url__startswith=BASE_URL).mock(return_value=httpx.Response(status))
    with pytest.raises(UnitSourceAuthError, match="token") as caught:
        _ = live().fetch("UDI", date(2026, 1, 1), date(2026, 1, 5))
    assert isinstance(caught.value, UnitSourceError)
    assert caught.value.status == 503
    assert caught.value.code == "UNIT_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("status", [400, 500])
def test_an_upstream_error_is_a_source_error(sie: respx.MockRouter, status: int):
    _ = sie.get(url__startswith=BASE_URL).mock(return_value=httpx.Response(status))
    with pytest.raises(UnitSourceError, match=f"HTTP {status}") as caught:
        _ = live().fetch("UDI", date(2026, 1, 1), date(2026, 1, 5))
    assert not isinstance(caught.value, UnitSourceAuthError)


def test_an_unreachable_api_is_a_source_error(sie: respx.MockRouter):
    _ = sie.get(url__startswith=BASE_URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(UnitSourceError, match="unreachable"):
        _ = live().fetch("UDI", date(2026, 1, 1), date(2026, 1, 5))


def test_a_malformed_body_is_a_source_error(sie: respx.MockRouter):
    _ = sie.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json={"bmx": {}})
    )
    with pytest.raises(UnitSourceError, match="unexpected format"):
        _ = live().fetch("UDI", date(2026, 1, 1), date(2026, 1, 5))


def test_parse_series_skips_unparsable_rows():
    payload = (
        b'{"bmx": {"series": [{"idSerie": "SP68257", "datos": ['
        b'{"fecha": "2026-01-01", "dato": "8.1"},'
        b'{"fecha": "02/01/2026", "dato": "abc"},'
        b'{"fecha": "03/01/2026", "dato": "8.3"}]},'
        b'{"idSerie": "SF43718"}]}}'
    )
    assert parse_series(payload) == {
        "SP68257": [(date(2026, 1, 3), Decimal("8.3"))],
        "SF43718": [],
    }


def test_long_ranges_are_split_into_366_day_windows(sie: respx.MockRouter):
    empty = {"bmx": {"series": [{"idSerie": "SP68257", "datos": []}]}}
    first = sie.get(f"{BASE_URL}/series/SP68257/datos/2025-01-01/2026-01-01").mock(
        return_value=httpx.Response(200, json=empty)
    )
    second = sie.get(f"{BASE_URL}/series/SP68257/datos/2026-01-02/2026-12-31").mock(
        return_value=httpx.Response(200, json=empty)
    )
    assert live().fetch("UDI", date(2025, 1, 1), date(2026, 12, 31)) == []
    assert first.call_count == 1
    assert second.call_count == 1
    assert len(sie.calls) == 2


def test_latest_available_reads_oportuno(sie: respx.MockRouter):
    body = {
        "bmx": {
            "series": [
                {
                    "idSerie": "SP68257",
                    "datos": [{"fecha": "20/01/2026", "dato": "8.700000"}],
                }
            ]
        }
    }
    _ = sie.get(f"{BASE_URL}/series/SP68257/datos/oportuno").mock(
        return_value=httpx.Response(200, json=body)
    )
    latest = live().latest_available("UDI")
    assert latest is not None
    assert latest.valid_from == date(2026, 1, 20)
    assert latest.value == Decimal("8.700000")
    assert latest.source == "BANXICO_SIE:SP68257"


def test_latest_available_is_none_when_the_series_is_empty(sie: respx.MockRouter):
    _ = sie.get(f"{BASE_URL}/series/SP68257/datos/oportuno").mock(
        return_value=httpx.Response(200, json={"bmx": {"series": []}})
    )
    assert live().latest_available("UDI") is None


def test_demo_mode_reads_the_seeds():
    source = BanxicoSieSource(None, seed_dir=SEEDS, env="dev")
    assert source.mode == "demo"
    rows = source.fetch("UDI", date(2026, 1, 1), date(2026, 1, 5))
    assert len(rows) == 5
    latest = source.latest_available("UDI")
    assert latest is not None
    assert latest.valid_from == date(2026, 1, 31)


def test_demo_mode_is_refused_in_prod(sie: respx.MockRouter, tmp_path: Path):
    # An empty seed directory: a refusal that read the seeds would fail differently.
    source = BanxicoSieSource(None, seed_dir=tmp_path, env="prod")
    assert source.mode == "demo"
    with pytest.raises(UnitSourceError, match="XOLOTL_ENV=prod"):
        _ = source.fetch("UDI", date(2026, 1, 1), date(2026, 1, 5))
    with pytest.raises(UnitSourceError, match="XOLOTL_ENV=prod"):
        _ = source.latest_available("UDI")

    route = sie.get(f"{BASE_URL}/series/SP68257/datos/2026-01-01/2026-01-05").mock(
        return_value=httpx.Response(200, json={"bmx": {"series": []}})
    )
    assert live(env="prod").fetch("UDI", date(2026, 1, 1), date(2026, 1, 5)) == []
    assert route.call_count == 1


def test_uma_has_no_series(sie: respx.MockRouter):
    with pytest.raises(ValidationFailedError, match="no Banxico series") as caught:
        _ = live().fetch("UMA", date(2026, 1, 1), date(2026, 1, 5))
    assert caught.value.status == 422
    with pytest.raises(ValidationFailedError):
        _ = live().latest_available("UMA")
    with pytest.raises(ValidationFailedError):
        _ = live().fetch("UDI", date(2026, 1, 5), date(2026, 1, 1))
    assert len(sie.calls) == 0
