from pathlib import Path

import yaml

PACKS = Path(__file__).resolve().parents[1] / "policy-packs"


def test_the_jurisdictions_seed_parses():
    data = yaml.safe_load((PACKS / "_jurisdictions.yaml").read_text(encoding="utf-8"))
    assert data["confidence"] in {"VERIFIED", "UNVERIFIED"}
    assert data["call_for_action"] == ["KP", "IR", "MM"]
    codes = data["increased_monitoring"]
    assert all(len(code) == 2 and code.isupper() for code in codes)


def test_the_typology_library_holds_one_quoted_file_per_template():
    # The typologies live beside the packs; the pack loader skips "_" directories.
    files = sorted((PACKS / "MX" / "_typologies").glob("*.yaml"))
    assert len(files) == 16
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert set(data) == {"version", "country_code", "packs", "definition"}
        assert data["definition"]["code"] == path.stem
        # YAML 1.1 reads a bare "on" key as true, so the files quote it.
        assert True not in data["definition"]
