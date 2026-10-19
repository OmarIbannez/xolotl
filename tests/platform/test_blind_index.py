"""Blind indexes (spec 8.2, plan 1b task B3): normalise, then HMAC under the INDEX key.

``normalize_identifier`` is a pure function and is pinned case by case. ``blind_index``
runs against the real database through a ``LocalKeyProvider``, so the tags depend on
real tenant keys, real row-level security and the real provider cache.
"""

import hashlib
import hmac
import logging
from collections.abc import Generator
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from tests.helpers import leaked, make_tenant, make_tenant_with_keys, system_ctx
from xolotl.platform.crypto.blind_index import blind_index, normalize_identifier
from xolotl.platform.crypto.errors import (
    DecryptionError,
    KeyDestroyedError,
    KeyNotFoundError,
)
from xolotl.platform.crypto.provider import (
    KeyProvider,
    LocalKeyProvider,
    get_key_provider,
    set_key_provider,
)
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import KeyPurpose

KEK = bytes(range(32, 64))
TAG_LEN = 32

# ---- normalisation (no database) ------------------------------------------------


# Fullwidth forms, other numeral systems and combining marks are built, not typed:
# they are the point of NFKC, and the lookalike characters cannot sit in the source.
def fullwidth(text: str) -> str:  # printable ASCII, as its fullwidth forms
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)


def arabic_indic(digits: str) -> str:
    zero = ord("\N{ARABIC-INDIC DIGIT ZERO}")
    return "".join(chr(zero + int(digit)) for digit in digits)


FULLWIDTH_EMAIL = fullwidth("ANA@acme.mx")
FULLWIDTH_PHONE = fullwidth("551234")
ARABIC_INDIC_PHONE = arabic_indic("5512")

NORMALISATION = [
    # email: trimmed, lower-cased, no whitespace anywhere; punctuation is kept
    pytest.param("email", " ana@Acme.MX ", "ana@acme.mx", id="email-trim-lowercase"),
    pytest.param("email", "Ana @ Acme .MX\t", "ana@acme.mx", id="email-inner-spaces"),
    pytest.param(
        "email", "ana\t@acme\n.mx", "ana@acme.mx", id="email-inner-tab-newline"
    ),
    pytest.param("email", "ana+tag@acme.mx", "ana+tag@acme.mx", id="email-plus-kept"),
    pytest.param("email", "a.b_c-d@acme.mx", "a.b_c-d@acme.mx", id="email-dots-kept"),
    pytest.param("email", FULLWIDTH_EMAIL, "ana@acme.mx", id="email-nfkc"),
    # phone: decimal digits only, written as ASCII; the country code is as given
    pytest.param("phone", "+52 (55) 1234-5678", "525512345678", id="phone-punctuation"),
    pytest.param("phone", "55 1234 5678", "5512345678", id="phone-no-country-code"),
    pytest.param("phone", FULLWIDTH_PHONE, "551234", id="phone-fullwidth-digits"),
    pytest.param("phone", ARABIC_INDIC_PHONE, "5512", id="phone-other-decimal-digits"),
    pytest.param("phone", "ext. 5 x", "5", id="phone-letters-dropped"),
    # name: upper-case, letters and digits of any script, single spaces, accents kept
    pytest.param("name", "gómez   pérez", "GÓMEZ PÉREZ", id="name-accents-kept"),
    pytest.param("name", "  ana\tmaría\n", "ANA MARÍA", id="name-whitespace-collapsed"),
    pytest.param("name", "O'Brien-Smith, Jr.", "OBRIENSMITH JR", id="name-punctuation"),
    pytest.param(
        "name",
        "Jose\N{COMBINING ACUTE ACCENT} Luis",
        "JOSÉ LUIS",
        id="name-combining-accent",
    ),
    pytest.param(
        "name",
        "José\N{NO-BREAK SPACE}Luis",
        "JOSÉ LUIS",
        id="name-no-break-space",
    ),
    pytest.param("name", "a - b", "A B", id="name-dash-between-spaces"),
    # curp, rfc, clabe and anything else: upper-case ASCII letters and digits only
    pytest.param("curp", " gaxx 800101-hdfrrr09 ", "GAXX800101HDFRRR09", id="curp"),
    pytest.param("rfc", "gaxx-800101.ab3", "GAXX800101AB3", id="rfc"),
    pytest.param("clabe", "0021 8000-1234 5678 91", "002180001234567891", id="clabe"),
    pytest.param("curp", "ñandú-1", "AND1", id="code-non-ascii-letters-dropped"),
    # an RFC keeps Ñ and & (the SAT alphabet is [A-ZÑ&]); CURP and CLABE do not
    pytest.param("rfc", "peña-800101-ab1", "PEÑA800101AB1", id="rfc-keeps-enye"),
    pytest.param("rfc", "a&b 010101 xyz", "A&B010101XYZ", id="rfc-keeps-ampersand"),
    pytest.param(
        "rfc",
        "pen\N{COMBINING TILDE}a800101ab1",
        "PEÑA800101AB1",
        id="rfc-composes-enye",
    ),
    pytest.param("rfc", fullwidth("A&B010101XYZ"), "A&B010101XYZ", id="rfc-nfkc"),
    pytest.param("rfc", "ñ&-#?", "Ñ&", id="rfc-other-punctuation-dropped"),
    pytest.param(
        "rfc", "peñá-800101-üb1", "PEÑ800101B1", id="rfc-other-accents-dropped"
    ),
    pytest.param("curp", "peña&800101hdfrrr09", "PEA800101HDFRRR09", id="curp-no-enye"),
    pytest.param("clabe", "0021&8000", "00218000", id="clabe-no-ampersand"),
    pytest.param("passport", "a&b-ñ", "AB", id="unknown-kind-no-enye-no-ampersand"),
    pytest.param("passport", "g-123 456", "G123456", id="unknown-kind-uses-default"),
    pytest.param("EMAIL", " Ana@Acme.MX", "ana@acme.mx", id="kind-case-insensitive"),
    pytest.param(" Name ", "ana maría", "ANA MARÍA", id="kind-trimmed"),
    pytest.param("email", "", "", id="empty-stays-empty"),
    pytest.param("rfc", "---", "", id="nothing-left-stays-empty"),
]


@pytest.mark.parametrize(("kind", "raw", "normalised"), NORMALISATION)
def test_normalize_identifier(kind, raw, normalised):
    assert normalize_identifier(kind, raw) == normalised


@pytest.mark.parametrize(("kind", "raw", "normalised"), NORMALISATION)
def test_normalising_twice_changes_nothing(kind, raw, normalised):
    assert normalize_identifier(kind, normalised) == normalised


# ---- the tags (real database) ---------------------------------------------------


@pytest.fixture
def provider(engine: Engine) -> Generator[LocalKeyProvider]:
    installed = LocalKeyProvider(KEK, get_sessionmaker())
    try:
        previous: KeyProvider | None = get_key_provider()
    except RuntimeError:
        previous = None
    set_key_provider(installed)
    yield installed
    set_key_provider(previous)


def test_equivalent_spellings_share_one_tag(provider: LocalKeyProvider):
    tenant = make_tenant_with_keys("alpha")
    assert blind_index("email", " ana@Acme.MX ", tenant_id=tenant) == blind_index(
        "email", "ana@acme.mx", tenant_id=tenant
    )
    assert blind_index("phone", "+52 (55) 1234-5678", tenant_id=tenant) == blind_index(
        "phone", "525512345678", tenant_id=tenant
    )


def test_the_tag_is_hmac_sha256_over_kind_and_normalised_value(
    provider: LocalKeyProvider,
):
    tenant = make_tenant_with_keys("alpha")
    key = provider.index_key(tenant)
    tag = blind_index("email", " Ana@ACME.mx ", tenant_id=tenant)
    assert tag == hmac.new(key, b"email:ana@acme.mx", hashlib.sha256).digest()
    assert len(tag) == TAG_LEN
    assert tag != hashlib.sha256(b"email:ana@acme.mx").digest()  # it is keyed


def test_different_values_give_different_tags(provider: LocalKeyProvider):
    tenant = make_tenant_with_keys("alpha")
    tags = {
        blind_index("email", value, tenant_id=tenant)
        for value in ("ana@acme.mx", "ana@acme.com", "anna@acme.mx")
    }
    assert len(tags) == 3


def test_different_kinds_give_different_tags_for_one_value(provider: LocalKeyProvider):
    tenant = make_tenant_with_keys("alpha")
    # curp and rfc normalise the same way, so only the kind prefix tells them apart
    assert normalize_identifier("curp", "ABC123") == normalize_identifier(
        "rfc", "ABC123"
    )
    assert blind_index("curp", "ABC123", tenant_id=tenant) != blind_index(
        "rfc", "ABC123", tenant_id=tenant
    )


def test_rfcs_that_differ_by_an_enye_or_an_ampersand_index_differently(
    provider: LocalKeyProvider,
):
    # Dropping the two characters would turn a persona fisica's RFC into the shape of a
    # persona moral's and make unrelated RFCs match.
    tenant = make_tenant_with_keys("alpha")
    assert blind_index("rfc", "PEÑA800101AB1", tenant_id=tenant) != blind_index(
        "rfc", "PEA800101AB1", tenant_id=tenant
    )
    assert blind_index("rfc", "A&B010101XYZ", tenant_id=tenant) != blind_index(
        "rfc", "AB010101XYZ", tenant_id=tenant
    )
    assert blind_index("rfc", "peña-800101-ab1", tenant_id=tenant) == blind_index(
        "rfc", " PEÑA800101AB1 ", tenant_id=tenant
    )


def test_a_kind_is_one_label_whatever_its_case(provider: LocalKeyProvider):
    tenant = make_tenant_with_keys("alpha")
    assert blind_index("Email", "ana@acme.mx", tenant_id=tenant) == blind_index(
        " email ", "ana@acme.mx", tenant_id=tenant
    )


def test_a_colon_in_a_kind_would_blur_the_boundary_with_the_value(
    provider: LocalKeyProvider,
):
    tenant = make_tenant_with_keys("alpha")
    with pytest.raises(ValueError, match="colon"):
        _ = blind_index("a:b", "c", tenant_id=tenant)


def test_different_tenants_give_different_tags(provider: LocalKeyProvider):
    alpha = make_tenant_with_keys("alpha")
    beta = make_tenant_with_keys("beta")
    assert blind_index("rfc", "XAXX010101000", tenant_id=alpha) != blind_index(
        "rfc", "XAXX010101000", tenant_id=beta
    )


def test_tags_are_stable_across_providers_sharing_the_kek_and_the_database(
    provider: LocalKeyProvider,
):
    tenant = make_tenant_with_keys("alpha")
    first = blind_index("curp", "GAXX800101HDFRRR09", tenant_id=tenant)
    successor = LocalKeyProvider(KEK, get_sessionmaker())  # a restarted process
    set_key_provider(successor)
    assert blind_index("curp", "GAXX800101HDFRRR09", tenant_id=tenant) == first


def test_a_provider_with_another_kek_cannot_index_the_tenant(
    provider: LocalKeyProvider,
):
    tenant = make_tenant_with_keys("alpha")
    set_key_provider(LocalKeyProvider(bytes(range(64, 96)), get_sessionmaker()))
    with pytest.raises(DecryptionError):
        _ = blind_index("curp", "GAXX800101HDFRRR09", tenant_id=tenant)


def test_rotating_the_data_key_leaves_the_tags_alone(provider: LocalKeyProvider):
    tenant = make_tenant_with_keys("alpha")
    before = blind_index("email", "ana@acme.mx", tenant_id=tenant)
    assert provider.rotate(tenant, KeyPurpose.DATA) == 2
    assert blind_index("email", "ana@acme.mx", tenant_id=tenant) == before


def test_a_tenant_without_keys_cannot_be_indexed(provider: LocalKeyProvider):
    tenant = make_tenant("alpha")  # no keys created
    with pytest.raises(KeyNotFoundError):
        _ = blind_index("email", "ana@acme.mx", tenant_id=tenant)


def test_a_tenant_whose_keys_were_destroyed_cannot_be_indexed(
    provider: LocalKeyProvider,
):
    tenant = make_tenant_with_keys("alpha")
    _ = blind_index("email", "ana@acme.mx", tenant_id=tenant)
    provider.destroy(tenant)
    with pytest.raises(KeyDestroyedError):
        _ = blind_index("email", "ana@acme.mx", tenant_id=tenant)


def test_the_callers_session_sees_keys_it_has_not_committed_yet(
    provider: LocalKeyProvider,
):
    tenant = make_tenant("alpha")
    with tenant_session(system_ctx(tenant)) as s:
        provider.create_tenant_keys(tenant, session=s)
        inside = blind_index("email", "ana@acme.mx", tenant_id=tenant, session=s)
        with pytest.raises(KeyNotFoundError):  # nobody else sees them, yet
            _ = blind_index("email", "ana@acme.mx", tenant_id=tenant)
    assert blind_index("email", "ana@acme.mx", tenant_id=tenant) == inside


@pytest.mark.parametrize(
    ("kind", "raw"),
    [("phone", "n/a, ext. x"), ("rfc", " - . - "), ("email", "  "), ("name", "")],
)
def test_a_value_with_nothing_left_to_index_is_refused(
    provider: LocalKeyProvider, kind, raw
):
    tenant = make_tenant_with_keys("alpha")
    with pytest.raises(ValueError, match="nothing left to index") as refused:
        _ = blind_index(kind, raw, tenant_id=tenant)
    assert str(refused.value) == f"{kind}: nothing left to index after normalisation"


def test_an_unknown_tenant_cannot_be_indexed(provider: LocalKeyProvider):
    with pytest.raises(KeyNotFoundError):
        _ = blind_index("email", "ana@acme.mx", tenant_id=uuid4())


# ---- secrecy --------------------------------------------------------------------


def test_nothing_is_logged_and_nothing_secret_is_in_an_error(
    provider: LocalKeyProvider, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    value = "ana.secret@acme.mx"
    tenant = make_tenant_with_keys("alpha")
    key = provider.index_key(tenant)
    tag = blind_index("email", value, tenant_id=tenant)
    errors = []
    for kind, raw in (("phone", "x" + value), ("a:b", value)):
        with pytest.raises(ValueError, match=r"nothing left|colon") as refused:
            _ = blind_index(kind, raw, tenant_id=tenant)
        errors.append(refused.value)
    provider.destroy(tenant)
    with pytest.raises(KeyDestroyedError) as destroyed:
        _ = blind_index("email", value, tenant_id=tenant)
    errors.append(destroyed.value)
    for error in errors:
        text = repr(error) + str(error) + repr(error.args)
        assert value not in text
        assert leaked(text, key, tag) == []
    assert value not in caplog.text
    assert leaked(caplog.text, key, tag) == []
