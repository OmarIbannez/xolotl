"""Masks for identifiers and names (no database)."""

from xolotl.modules.party.masking import initials, mask_identifier


def test_identifiers_keep_their_last_four_characters():
    assert mask_identifier("PEGA760510XY0") == "****0XY0"
    assert mask_identifier("1234") == "****"
    assert mask_identifier("ana@acme.mx") == "****e.mx"


def test_names_become_initials():
    assert initials("Ana María", "Pérez", "Gómez") == "A.M.P.G."
    assert initials("Acme Servicios SA de CV") == "A.S.S.D.C."
    assert not initials("")
    assert initials("  ana  ", "") == "A."
