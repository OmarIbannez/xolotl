from pathlib import Path

import yaml

PACKS = Path(__file__).resolve().parents[1] / "policy-packs"


def test_the_jurisdictions_seed_parses():
    data = yaml.safe_load((PACKS / "_jurisdictions.yaml").read_text(encoding="utf-8"))
    assert data["confidence"] in {"VERIFIED", "UNVERIFIED"}
    assert data["call_for_action"] == ["KP", "IR", "MM"]
    codes = data["increased_monitoring"]
    assert all(len(code) == 2 and code.isupper() for code in codes)
