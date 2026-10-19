from xolotl.platform.text import RFC_SHAPE, mask_tail


def test_rfc_shape_accepts_both_lengths_and_refuses_the_rest():
    # Persona moral, three letters; persona física, four.
    assert RFC_SHAPE.fullmatch("ACM010101AB1") is not None
    assert RFC_SHAPE.fullmatch("PEGA760510XY0") is not None
    assert RFC_SHAPE.fullmatch("Ñ&A010101AB1") is not None
    assert RFC_SHAPE.fullmatch("acm010101ab1") is None  # callers upper-case first
    assert RFC_SHAPE.fullmatch("ACM0101011") is None


def test_mask_tail_keeps_the_last_four_or_hides_everything():
    assert mask_tail("PEGA760510XY0") == "****0XY0"
    assert mask_tail("ana@acme.mx") == "****e.mx"
    assert mask_tail("1234") == "****"
    assert mask_tail("") == "****"
    assert mask_tail("ACM010101AB1", keep=3) == "****AB1"
