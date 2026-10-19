"""AES-256-GCM value envelope and data-key wrapping (spec 8.1, plan 1b task B2).

Three independent oracles keep the format honest: the raw ``cryptography`` library
(the layout is plain AES-GCM), a published AES-256-GCM test vector (the cipher is
the real one) and vectors frozen from the raw library for the platform's own layout
(version header, tenant and purpose as authenticated data).
"""

import logging
import traceback
from operator import eq
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tests.helpers import leaked, locals_report
from xolotl.platform.crypto import primitives
from xolotl.platform.crypto.errors import (
    DecryptionError,
    KeyDestroyedError,
    KeyNotFoundError,
)
from xolotl.platform.crypto.primitives import (
    HEADER_LEN,
    KEY_LEN,
    MAX_VERSION,
    NONCE_LEN,
    TAG_LEN,
    SecretBytes,
    aad_for,
    decrypt_value,
    encrypt_value,
    new_dek,
    parse_header,
    unwrap_dek,
    wrap_dek,
)
from xolotl.platform.enums import KeyPurpose
from xolotl.platform.errors import XolotlError

TENANT = UUID("018f3a52-9c1e-7b3a-8f2d-4c5e6a7b8c9d")
OTHER_TENANT = UUID("018f3a52-9c1e-7b3a-8f2d-4c5e6a7b8c9e")
DEK = bytes(range(32))
KEK = bytes(range(32, 64))
OTHER_KEY = bytes(range(64, 96))
PLAINTEXT = b"CURP:GOMC800101HDFRRR09"
AAD = TENANT.bytes + b"DATA"  # spelled out, so aad_for is checked against it

# Frozen with the raw library, never with the code under test: DEK, KEK, AAD and
# PLAINTEXT above, version 7, nonce bytes 100..111 (value) and 200..211 (wrap).
VALUE_V7 = bytes.fromhex("""
    00076465666768696a6b6c6d6e6f0b4e8c3643ae19d37d5a6fd8eb555bb50684
    5458d95cca3e3dbc40320e010c3e113560f5010bad
""")
WRAPPED_V7 = bytes.fromhex("""
    c8c9cacbcccdcecfd0d1d2d3a548ace1b753a4c5569689e6ee750bdb81d5216d
    c5e76ee645ec3fe1d56e7ba71630d07834b70b3dbc06722fb7c76fc1
""")
VALUE_NONCE = bytes(range(100, 112))
WRAP_NONCE = bytes(range(200, 212))


def wrapped(version: int = 7) -> bytes:
    return wrap_dek(
        KEK, DEK, tenant_id=TENANT, purpose=KeyPurpose.DATA, version=version
    )


# ---- vocabulary -----------------------------------------------------------------


def test_the_key_purposes_are_data_index_and_biometric():
    assert [purpose.value for purpose in KeyPurpose] == ["DATA", "INDEX", "BIOMETRIC"]
    assert KeyPurpose("DATA") is KeyPurpose.DATA


def test_layout_constants():
    assert (HEADER_LEN, NONCE_LEN, KEY_LEN, TAG_LEN, MAX_VERSION) == (
        2,
        12,
        32,
        16,
        65535,
    )


@pytest.mark.parametrize(
    ("cls", "status", "code"),
    [
        (DecryptionError, 500, "DECRYPTION_FAILED"),
        (KeyDestroyedError, 410, "KEY_DESTROYED"),
        (KeyNotFoundError, 500, "KEY_NOT_FOUND"),
    ],
)
def test_crypto_errors_are_xolotl_errors_with_a_stable_code(cls, status, code):
    error = cls()
    assert isinstance(error, XolotlError)
    assert (error.status, error.code) == (status, code)
    assert error.detail
    assert str(error) == error.detail


def test_aad_is_the_tenant_bytes_followed_by_the_purpose():
    assert aad_for(TENANT, KeyPurpose.DATA) == TENANT.bytes + b"DATA"
    assert aad_for(TENANT, KeyPurpose.BIOMETRIC) == TENANT.bytes + b"BIOMETRIC"
    every = {aad_for(t, p) for t in (TENANT, OTHER_TENANT) for p in KeyPurpose}
    assert len(every) == 6


def test_new_dek_is_32_random_bytes():
    keys = [new_dek() for _ in range(20)]
    assert {len(key) for key in keys} == {KEY_LEN}
    assert len(set(keys)) == 20


# ---- values ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "plaintext",
    [b"", b"x", PLAINTEXT, bytes(range(256)) * 4],
    ids=["empty", "one-byte", "curp", "one-kib"],
)
def test_a_value_round_trips(plaintext):
    ciphertext = encrypt_value(DEK, version=1, plaintext=plaintext, aad=AAD)
    assert len(ciphertext) == HEADER_LEN + NONCE_LEN + len(plaintext) + TAG_LEN
    assert decrypt_value(DEK, ciphertext=ciphertext, aad=AAD) == plaintext


def test_the_plaintext_is_not_visible_in_the_ciphertext():
    ciphertext = encrypt_value(DEK, version=1, plaintext=PLAINTEXT, aad=AAD)
    assert PLAINTEXT not in ciphertext
    assert PLAINTEXT[:8] not in ciphertext


def test_every_encryption_uses_a_fresh_nonce():
    ciphertexts = [
        encrypt_value(DEK, version=1, plaintext=PLAINTEXT, aad=AAD) for _ in range(50)
    ]
    nonces = {ct[HEADER_LEN : HEADER_LEN + NONCE_LEN] for ct in ciphertexts}
    assert len(nonces) == 50
    assert len(set(ciphertexts)) == 50


@pytest.mark.parametrize("version", [0, 1, 2, 255, 256, 0x1234, MAX_VERSION])
def test_the_header_carries_the_version_big_endian(version):
    ciphertext = encrypt_value(DEK, version=version, plaintext=PLAINTEXT, aad=AAD)
    assert ciphertext[:HEADER_LEN] == version.to_bytes(2, "big")
    assert parse_header(ciphertext) == version


@pytest.mark.parametrize("version", [-1, MAX_VERSION + 1, 2**32])
def test_a_version_that_does_not_fit_the_header_is_refused(version):
    with pytest.raises(ValueError, match="version"):
        _ = encrypt_value(DEK, version=version, plaintext=PLAINTEXT, aad=AAD)
    with pytest.raises(ValueError, match="version"):
        _ = wrapped(version)


def test_parse_header_reads_two_bytes_and_nothing_else():
    assert parse_header(b"\x12\x34rest") == 0x1234
    assert parse_header(b"\x00\x2a") == 42


@pytest.mark.parametrize("short", [b"", b"\x00", b"\xff"])
def test_parse_header_refuses_input_shorter_than_the_header(short):
    with pytest.raises(DecryptionError):
        _ = parse_header(short)


def test_the_value_is_plain_aes_256_gcm_over_the_tenant_and_purpose():
    ciphertext = encrypt_value(DEK, version=3, plaintext=PLAINTEXT, aad=AAD)
    nonce = ciphertext[HEADER_LEN : HEADER_LEN + NONCE_LEN]
    body = ciphertext[HEADER_LEN + NONCE_LEN :]
    assert AESGCM(DEK).decrypt(nonce, body, AAD) == PLAINTEXT


def test_the_cipher_is_the_published_aes_256_gcm():
    # McGrew and Viega, "The Galois/Counter Mode of Operation (GCM)", test case 16:
    # AES-256, 96-bit IV, 60-byte plaintext, 20-byte additional authenticated data.
    key = bytes.fromhex(
        "feffe9928665731c6d6a8f9467308308feffe9928665731c6d6a8f9467308308"
    )
    iv = bytes.fromhex("cafebabefacedbaddecaf888")
    plaintext = bytes.fromhex("""
        d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72
        1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b39
    """)
    aad = bytes.fromhex("feedfacedeadbeeffeedfacedeadbeefabaddad2")
    body = bytes.fromhex("""
        522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa
        8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f662
    """)
    tag = bytes.fromhex("76fc6ece0f4e1768cddf8853bb2d551b")
    blob = (1).to_bytes(HEADER_LEN, "big") + iv + body + tag
    assert decrypt_value(key, ciphertext=blob, aad=aad) == plaintext


def test_a_frozen_value_still_decrypts():
    assert parse_header(VALUE_V7) == 7
    aad = aad_for(TENANT, KeyPurpose.DATA)
    assert decrypt_value(DEK, ciphertext=VALUE_V7, aad=aad) == PLAINTEXT


def test_encrypting_with_the_frozen_nonce_gives_the_frozen_value(monkeypatch):
    monkeypatch.setattr(primitives, "_new_nonce", lambda: VALUE_NONCE)
    ciphertext = encrypt_value(DEK, version=7, plaintext=PLAINTEXT, aad=AAD)
    assert ciphertext == VALUE_V7


@pytest.mark.parametrize(
    "aad",
    [
        OTHER_TENANT.bytes + b"DATA",
        TENANT.bytes + b"INDEX",
        TENANT.bytes + b"DAT",
        TENANT.bytes + b"DATA\x00",
        b"DATA",
        b"",
    ],
    ids=[
        "other-tenant",
        "other-purpose",
        "short-purpose",
        "suffix",
        "no-tenant",
        "empty",
    ],
)
def test_a_value_does_not_decrypt_under_other_authenticated_data(aad):
    with pytest.raises(DecryptionError):
        _ = decrypt_value(DEK, ciphertext=VALUE_V7, aad=aad)


def test_a_value_does_not_decrypt_under_another_key():
    with pytest.raises(DecryptionError):
        _ = decrypt_value(OTHER_KEY, ciphertext=VALUE_V7, aad=AAD)


def test_flipping_any_bit_after_the_header_is_detected():
    for index in range(HEADER_LEN, len(VALUE_V7)):
        for mask in (0x01, 0x80):
            tampered = bytearray(VALUE_V7)
            tampered[index] ^= mask
            with pytest.raises(DecryptionError):
                _ = decrypt_value(DEK, ciphertext=bytes(tampered), aad=AAD)


def test_a_truncated_value_is_refused_at_every_length():
    for length in range(len(VALUE_V7)):
        with pytest.raises(DecryptionError):
            _ = decrypt_value(DEK, ciphertext=VALUE_V7[:length], aad=AAD)


def test_a_value_with_trailing_bytes_is_refused():
    with pytest.raises(DecryptionError):
        _ = decrypt_value(DEK, ciphertext=VALUE_V7 + b"\x00", aad=AAD)


# ---- wrapped data keys ----------------------------------------------------------


def test_a_data_key_round_trips_through_wrapping():
    blob = wrapped(7)
    assert len(blob) == NONCE_LEN + KEY_LEN + TAG_LEN
    assert DEK not in blob
    unwrapped = unwrap_dek(
        KEK, blob, tenant_id=TENANT, purpose=KeyPurpose.DATA, version=7
    )
    assert unwrapped == DEK


def test_every_wrap_uses_a_fresh_nonce():
    blobs = [wrapped() for _ in range(50)]
    assert len({blob[:NONCE_LEN] for blob in blobs}) == 50


def test_a_wrapped_key_is_aes_gcm_over_tenant_purpose_and_big_endian_version():
    blob = wrapped(7)
    aad = TENANT.bytes + b"DATA" + b"\x00\x07"
    assert AESGCM(KEK).decrypt(blob[:NONCE_LEN], blob[NONCE_LEN:], aad) == DEK


def test_a_frozen_wrapped_key_still_unwraps():
    unwrapped = unwrap_dek(
        KEK, WRAPPED_V7, tenant_id=TENANT, purpose=KeyPurpose.DATA, version=7
    )
    assert unwrapped == DEK


def test_wrapping_with_the_frozen_nonce_gives_the_frozen_blob(monkeypatch):
    monkeypatch.setattr(primitives, "_new_nonce", lambda: WRAP_NONCE)
    assert wrapped(7) == WRAPPED_V7


@pytest.mark.parametrize(
    ("tenant_id", "purpose", "version"),
    [
        (OTHER_TENANT, KeyPurpose.DATA, 7),
        (TENANT, KeyPurpose.INDEX, 7),
        (TENANT, KeyPurpose.DATA, 8),
        (TENANT, KeyPurpose.DATA, 0x0700),
    ],
    ids=["other-tenant", "other-purpose", "next-version", "byte-swapped-version"],
)
def test_a_wrapped_key_is_bound_to_its_tenant_purpose_and_version(
    tenant_id, purpose, version
):
    with pytest.raises(DecryptionError):
        _ = unwrap_dek(
            KEK, WRAPPED_V7, tenant_id=tenant_id, purpose=purpose, version=version
        )


def test_a_wrapped_key_does_not_unwrap_under_another_kek():
    with pytest.raises(DecryptionError):
        _ = unwrap_dek(
            OTHER_KEY, WRAPPED_V7, tenant_id=TENANT, purpose=KeyPurpose.DATA, version=7
        )


def test_flipping_any_bit_of_a_wrapped_key_is_detected():
    for index in range(len(WRAPPED_V7)):
        tampered = bytearray(WRAPPED_V7)
        tampered[index] ^= 0x01
        with pytest.raises(DecryptionError):
            _ = unwrap_dek(
                KEK,
                bytes(tampered),
                tenant_id=TENANT,
                purpose=KeyPurpose.DATA,
                version=7,
            )


def test_a_truncated_or_extended_wrapped_key_is_refused():
    for blob in [*(WRAPPED_V7[:n] for n in range(len(WRAPPED_V7))), WRAPPED_V7 + b"\0"]:
        with pytest.raises(DecryptionError):
            _ = unwrap_dek(
                KEK, blob, tenant_id=TENANT, purpose=KeyPurpose.DATA, version=7
            )


def test_a_wrapped_payload_that_is_not_a_32_byte_key_is_refused():
    nonce = bytes(NONCE_LEN)
    aad = TENANT.bytes + b"DATA" + (7).to_bytes(2, "big")
    blob = nonce + AESGCM(KEK).encrypt(nonce, b"k" * 16, aad)  # authentic, wrong size
    with pytest.raises(DecryptionError):
        _ = unwrap_dek(KEK, blob, tenant_id=TENANT, purpose=KeyPurpose.DATA, version=7)


# ---- misuse and secrecy ---------------------------------------------------------


@pytest.mark.parametrize("key", [b"", b"k" * 16, b"k" * 24, b"k" * 31, b"k" * 33])
def test_keys_that_are_not_32_bytes_are_refused(key):
    # 16 and 24 bytes are valid AES keys: without the check they would silently
    # select AES-128 or AES-192.
    purpose = KeyPurpose.DATA
    calls = [
        lambda: encrypt_value(key, version=1, plaintext=PLAINTEXT, aad=AAD),
        lambda: decrypt_value(key, ciphertext=VALUE_V7, aad=AAD),
        lambda: wrap_dek(key, DEK, tenant_id=TENANT, purpose=purpose, version=7),
        lambda: wrap_dek(KEK, key, tenant_id=TENANT, purpose=purpose, version=7),
        lambda: unwrap_dek(
            key, WRAPPED_V7, tenant_id=TENANT, purpose=purpose, version=7
        ),
    ]
    for call in calls:
        with pytest.raises(ValueError, match="32 bytes"):
            _ = call()


def test_failures_never_carry_key_material():
    calls = [
        lambda: decrypt_value(OTHER_KEY, ciphertext=VALUE_V7, aad=AAD),
        lambda: decrypt_value(DEK, ciphertext=VALUE_V7[:20], aad=AAD),
        lambda: unwrap_dek(
            OTHER_KEY, WRAPPED_V7, tenant_id=TENANT, purpose=KeyPurpose.DATA, version=7
        ),
        lambda: encrypt_value(DEK[:16], version=1, plaintext=PLAINTEXT, aad=AAD),
        lambda: wrap_dek(
            KEK[:16], DEK, tenant_id=TENANT, purpose=KeyPurpose.DATA, version=7
        ),
    ]
    for call in calls:
        with pytest.raises((DecryptionError, ValueError)) as caught:
            _ = call()
        report = "".join(traceback.format_exception(caught.value))
        report += repr(caught.value) + str(caught.value) + repr(caught.value.args)
        assert leaked(report, DEK, KEK, OTHER_KEY, PLAINTEXT) == []


# ---- masked key bytes -----------------------------------------------------------


def test_secret_bytes_print_as_a_mask_whichever_way_they_are_formatted():
    secret = SecretBytes(DEK)
    texts = [
        repr(secret),
        str(secret),
        f"{secret}",
        f"{secret!r}",
        format(secret, ""),
        str([secret]),
        repr({"key": secret}),
        str((secret, 1)),
    ]
    for text in texts:
        assert "SecretBytes(***)" in text
        assert leaked(text, DEK) == []


def test_secret_bytes_stay_masked_in_log_lines(caplog: pytest.LogCaptureFixture):
    secret = SecretBytes(DEK)
    logging.getLogger("secret-test").warning("key=%s repr=%r", secret, secret)
    assert "key=SecretBytes(***) repr=SecretBytes(***)" in caplog.text
    assert leaked(caplog.text, DEK) == []


def test_secret_bytes_are_bytes_in_every_other_respect():
    secret = SecretBytes(DEK)
    assert isinstance(secret, bytes)
    assert secret == DEK
    assert eq(DEK, secret)  # equal from either side
    assert hash(secret) == hash(DEK)
    assert {DEK: "plain"}[secret] == "plain"
    assert len(secret) == KEY_LEN
    assert secret[:4] == DEK[:4]
    assert secret.hex() == DEK.hex()  # revealing it is still possible, but explicit
    nonce, body = VALUE_V7[HEADER_LEN : HEADER_LEN + NONCE_LEN], VALUE_V7[14:]
    assert AESGCM(secret).decrypt(nonce, body, AAD) == PLAINTEXT  # the cipher takes it


def test_secret_bytes_of_masks_plain_bytes_and_keeps_a_secret_as_it_is():
    masked = SecretBytes.of(DEK)
    assert type(masked) is SecretBytes
    assert masked == DEK
    assert SecretBytes.of(masked) is masked


def test_the_keys_the_primitives_hand_out_are_masked():
    assert type(new_dek()) is SecretBytes
    blob = wrapped()
    assert type(blob) is SecretBytes
    unwrapped = unwrap_dek(
        KEK, blob, tenant_id=TENANT, purpose=KeyPurpose.DATA, version=7
    )
    assert type(unwrapped) is SecretBytes
    assert unwrapped == DEK


# Every way a primitive can fail, with the raw keys passed in. A tracker that captures
# local variables must see masks where the keys were, not the keys.
FAILURES = {
    "decrypt under another key": lambda: decrypt_value(
        OTHER_KEY, ciphertext=VALUE_V7, aad=AAD
    ),
    "decrypt under other authenticated data": lambda: decrypt_value(
        DEK, ciphertext=VALUE_V7, aad=b""
    ),
    "decrypt a truncated value": lambda: decrypt_value(
        DEK, ciphertext=VALUE_V7[:20], aad=AAD
    ),
    "decrypt with a key that is too long": lambda: decrypt_value(
        DEK + b"!", ciphertext=VALUE_V7, aad=AAD
    ),
    "encrypt with a key that is too short": lambda: encrypt_value(
        DEK[:16], version=1, plaintext=PLAINTEXT, aad=AAD
    ),
    "encrypt with a version that does not fit": lambda: encrypt_value(
        DEK, version=-1, plaintext=PLAINTEXT, aad=AAD
    ),
    "unwrap under another kek": lambda: unwrap_dek(
        OTHER_KEY, WRAPPED_V7, tenant_id=TENANT, purpose=KeyPurpose.DATA, version=7
    ),
    "unwrap for another tenant": lambda: unwrap_dek(
        KEK, WRAPPED_V7, tenant_id=OTHER_TENANT, purpose=KeyPurpose.DATA, version=7
    ),
    "unwrap a truncated blob": lambda: unwrap_dek(
        KEK, WRAPPED_V7[:30], tenant_id=TENANT, purpose=KeyPurpose.DATA, version=7
    ),
    "unwrap under a kek that is too short": lambda: unwrap_dek(
        KEK[:16], WRAPPED_V7, tenant_id=TENANT, purpose=KeyPurpose.DATA, version=7
    ),
    "wrap under a kek that is too short": lambda: wrap_dek(
        KEK[:16], DEK, tenant_id=TENANT, purpose=KeyPurpose.DATA, version=7
    ),
    "wrap a key that is too short": lambda: wrap_dek(
        KEK, DEK[:24], tenant_id=TENANT, purpose=KeyPurpose.DATA, version=7
    ),
    "wrap with a version that does not fit": lambda: wrap_dek(
        KEK, DEK, tenant_id=TENANT, purpose=KeyPurpose.DATA, version=MAX_VERSION + 1
    ),
}
EVERY_KEY = [DEK, KEK, OTHER_KEY, WRAPPED_V7, DEK[:16], DEK[:24], DEK + b"!", KEK[:16]]


@pytest.mark.parametrize("name", list(FAILURES))
def test_tracebacks_that_capture_locals_show_masks_not_key_bytes(name: str):
    report = locals_report(FAILURES[name])
    assert leaked(report, *EVERY_KEY) == []
    assert "SecretBytes(***)" in report  # the key arguments are in the frames, masked
