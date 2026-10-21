"""The jurisdiction lists file and its loader (spec §9.1, plan 4b Deviation 15)."""

import hashlib
from datetime import date
from pathlib import Path

import pytest

from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.policy import JurisdictionLists, load_jurisdictions
from xolotl.platform.policy.jurisdictions import (
    COUNTRY_SETS,
    JURISDICTIONS_FILE,
    REGION_SETS,
)

_VALID = """\
as_of: 2026-06-19
source: test
confidence: VERIFIED
call_for_action: [KP]
increased_monitoring: [VE, YE]
tax_haven: [KY]
regions_confidence: UNVERIFIED
regions:
  BORDER_ZONE_MX: [MX-BCN, MX-SON]
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "_jurisdictions.yaml"
    _ = path.write_text(text, encoding="utf-8")
    return path


def test_the_shipped_file_loads_with_the_four_sets() -> None:
    lists = load_jurisdictions()
    assert isinstance(lists, JurisdictionLists)
    assert lists.as_of == date(2026, 6, 19)
    assert set(lists.countries) == set(COUNTRY_SETS)
    assert set(lists.regions) == set(REGION_SETS)
    assert lists.countries["FATF_BLACK"] == {"KP", "IR", "MM"}
    assert len(lists.countries["FATF_GREY"]) == 22
    assert lists.countries["TAX_HAVEN"] == frozenset()
    assert lists.regions["BORDER_ZONE_MX"] == {
        "MX-BCN",
        "MX-SON",
        "MX-CHH",
        "MX-COA",
        "MX-NLE",
        "MX-TAM",
    }
    expected = hashlib.sha256(JURISDICTIONS_FILE.read_bytes()).digest()
    assert lists.content_hash == expected


def test_names_resolve_with_or_without_the_dollar() -> None:
    lists = load_jurisdictions()
    union = lists.resolve_countries(["$FATF_GREY", "FATF_BLACK"])
    assert union == lists.countries["FATF_GREY"] | lists.countries["FATF_BLACK"]
    assert lists.resolve_regions(["$BORDER_ZONE_MX"]) == lists.regions["BORDER_ZONE_MX"]
    with pytest.raises(ValueError, match="NOWHERE"):
        _ = lists.resolve_countries(["NOWHERE"])
    with pytest.raises(ValueError, match="FATF_GREY"):
        _ = lists.resolve_regions(["$FATF_GREY"])


def test_a_malformed_code_is_refused(tmp_path: Path) -> None:
    assert load_jurisdictions(_write(tmp_path, _VALID)).countries["TAX_HAVEN"] == {"KY"}
    with pytest.raises(ValidationFailedError, match="MEX"):
        _ = load_jurisdictions(_write(tmp_path, _VALID.replace("[KY]", "[MEX]")))
    with pytest.raises(ValidationFailedError, match="BCN"):
        _ = load_jurisdictions(_write(tmp_path, _VALID.replace("MX-BCN", "BCN")))
    with pytest.raises(ValidationFailedError, match="tax_haven"):
        _ = load_jurisdictions(_write(tmp_path, _VALID.replace("tax_haven", "x")))
    with pytest.raises(ValidationFailedError, match="as_of"):
        _ = load_jurisdictions(_write(tmp_path, _VALID.replace("2026-06-19", "soon")))
    with pytest.raises(ValidationFailedError, match="mapping"):
        _ = load_jurisdictions(_write(tmp_path, "- just a list\n"))
    with pytest.raises(ValidationFailedError, match="not YAML"):
        _ = load_jurisdictions(_write(tmp_path, "as_of: [unclosed\n"))
    with pytest.raises(ValidationFailedError, match="list of codes"):
        _ = load_jurisdictions(_write(tmp_path, _VALID.replace("[KY]", "KY")))
    regions = _VALID.index("regions:\n")
    with pytest.raises(ValidationFailedError, match="mapping of named sets"):
        _ = load_jurisdictions(_write(tmp_path, _VALID[:regions] + "regions: []\n"))
    with pytest.raises(ValidationFailedError, match="missing BORDER_ZONE_MX"):
        _ = load_jurisdictions(_write(tmp_path, _VALID[:regions] + "regions: {}\n"))
    with pytest.raises(ValidationFailedError, match="unknown sets COAST"):
        _ = load_jurisdictions(_write(tmp_path, _VALID + "  COAST: [MX-GRO]\n"))
