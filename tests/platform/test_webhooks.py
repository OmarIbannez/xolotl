"""Webhooks: the contract receivers implement (spec §9.2).

The secret, the signature, the timestamp tolerance and the retry schedule. Nothing here
touches the database: the URL rules are in ``test_webhook_urls.py``, the fan-out in
``test_webhook_fanout.py`` and the delivery, its retries and dead letters in
``test_webhook_delivery.py``.
"""

import hashlib
import hmac
import re
import secrets
import time
from datetime import UTC, datetime, timedelta

import pytest

from xolotl.platform import clock
from xolotl.platform.events.webhooks import (
    DEFAULT_TOLERANCE,
    EVENT_ID_HEADER,
    EVENT_TYPE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    backoff,
    generate_webhook_secret,
    sign,
    verify_signature,
)

# ---- the secret ------------------------------------------------------------------


def test_a_secret_is_whsec_and_32_hex_digits():
    assert re.fullmatch(r"whsec_[0-9a-f]{32}", generate_webhook_secret())


def test_secrets_are_not_repeated():
    assert len({generate_webhook_secret() for _ in range(200)}) == 200


def test_a_secret_is_16_random_bytes_from_the_secrets_module(monkeypatch):
    asked: list[int] = []

    def token_hex(nbytes: int) -> str:
        asked.append(nbytes)
        return "ab" * nbytes

    monkeypatch.setattr(secrets, "token_hex", token_hex)
    assert generate_webhook_secret() == "whsec_" + "ab" * 16
    assert asked == [16]


# ---- the signature ---------------------------------------------------------------

SECRET = "whsec_0123456789abcdef0123456789abcdef"
BODY = b'{"id":"evt_1"}'
STAMP = 1760875200
NOW = datetime.fromtimestamp(STAMP, UTC)
# Computed with ``openssl dgst -sha256 -hmac`` over ``1760875200.{"id":"evt_1"}``, so
# the contract is checked against a tool that shares no code with the platform.
SIGNED = "v1=12d0cec80757cc96696df0e818c6e79eb73226e2dbd22a11bd304945fc038835"
SIGNED_EMPTY = "v1=dca51b8be5f2ef3ab80fbb1c96a1495a50ea5d481a678d8adfb8d6854a92f833"


def test_the_headers_are_the_ones_receivers_are_told_about():
    assert EVENT_ID_HEADER == "X-Xolotl-Event-Id"
    assert EVENT_TYPE_HEADER == "X-Xolotl-Event-Type"
    assert TIMESTAMP_HEADER == "X-Xolotl-Timestamp"
    assert SIGNATURE_HEADER == "X-Xolotl-Signature"


def test_a_signature_matches_one_computed_by_another_tool():
    assert sign(SECRET, STAMP, BODY) == SIGNED
    assert sign(SECRET, STAMP, b"") == SIGNED_EMPTY


def test_a_signature_is_v1_and_the_hmac_of_the_timestamp_a_dot_and_the_body():
    digest = hmac.new(SECRET.encode(), b"1760875200." + BODY, hashlib.sha256)
    assert sign(SECRET, STAMP, BODY) == f"v1={digest.hexdigest()}"


def test_the_body_is_signed_as_the_bytes_it_is():
    # No decoding, no re-serialisation: a receiver verifies what it received.
    pretty = b'{\n  "id": "evt_1"\n}'
    odd = b'{"name":"\xff\xfe not utf-8"}'
    assert sign(SECRET, STAMP, pretty) != sign(SECRET, STAMP, BODY)
    assert (
        sign(SECRET, STAMP, odd)
        == "v1="
        + hmac.new(SECRET.encode(), b"1760875200." + odd, hashlib.sha256).hexdigest()
    )


def test_the_signature_depends_on_the_secret_the_timestamp_and_the_body():
    base = sign(SECRET, STAMP, BODY)
    assert sign(SECRET.replace("0", "1", 1), STAMP, BODY) != base
    assert sign(SECRET, STAMP + 1, BODY) != base
    assert sign(SECRET, STAMP, BODY + b" ") != base


def test_a_valid_signature_verifies():
    assert verify_signature(SECRET, SIGNED, STAMP, BODY, now=NOW)
    assert verify_signature(SECRET, SIGNED_EMPTY, STAMP, b"", now=NOW)


def test_the_timestamp_may_be_the_header_text():
    assert verify_signature(SECRET, SIGNED, str(STAMP), BODY, now=NOW)


def test_a_signature_over_anything_else_is_refused():
    assert not verify_signature(SECRET, SIGNED, STAMP, BODY + b" ", now=NOW)
    assert not verify_signature(SECRET, SIGNED, STAMP + 1, BODY, now=NOW)
    assert not verify_signature("whsec_" + "0" * 32, SIGNED, STAMP, BODY, now=NOW)
    assert not verify_signature(SECRET, SIGNED[:-1] + "6", STAMP, BODY, now=NOW)


@pytest.mark.parametrize(
    "header",
    [
        "",
        "v1=",
        SIGNED.removeprefix("v1="),  # no version
        SIGNED.replace("v1=", "v2="),  # a version nobody speaks yet
        SIGNED.replace("v1=", "V1="),
        SIGNED.upper(),
        SIGNED[:-2],  # truncated
        SIGNED + "0",  # padded
        "sha256=" + SIGNED.removeprefix("v1="),
    ],
)
def test_a_header_that_is_not_a_v1_signature_is_refused(header):
    assert not verify_signature(SECRET, header, STAMP, BODY, now=NOW)


def test_any_one_v1_entry_of_the_header_may_match():
    # Room for a secret rotation that signs with both for a while.
    wrong = "v1=" + "0" * 64
    assert verify_signature(SECRET, f"{wrong},{SIGNED}", STAMP, BODY, now=NOW)
    assert verify_signature(SECRET, f"{SIGNED},{wrong}", STAMP, BODY, now=NOW)
    assert verify_signature(SECRET, f"v2=abc, {SIGNED} ", STAMP, BODY, now=NOW)
    assert not verify_signature(SECRET, f"{wrong},v2=abc", STAMP, BODY, now=NOW)


@pytest.mark.parametrize(
    "stamp", ["", "abc", "-5", "1e9", " 1760875200", "+5", "١٧٦٠٨٧٥٢٠٠"]
)
def test_a_timestamp_that_is_not_plain_digits_is_refused(stamp):
    assert not verify_signature(SECRET, SIGNED, stamp, BODY, now=NOW)


@pytest.mark.parametrize("stamp", ["-5", "+5"])
def test_a_timestamp_with_a_sign_is_refused_whatever_the_clock_says(stamp):
    # Near the epoch these are within the tolerance, so only their format refuses them.
    signed = sign(SECRET, int(stamp), BODY)
    near_zero = datetime.fromtimestamp(3, UTC)
    assert not verify_signature(SECRET, signed, stamp, BODY, now=near_zero)


def test_a_timestamp_with_absurdly_many_digits_is_refused_without_raising():
    assert not verify_signature(SECRET, SIGNED, "9" * 5000, BODY, now=NOW)


def test_the_tolerance_is_five_minutes_either_side_by_default():
    assert timedelta(minutes=5) == DEFAULT_TOLERANCE
    edge = timedelta(minutes=5)
    assert verify_signature(SECRET, SIGNED, STAMP, BODY, now=NOW + edge)
    assert verify_signature(SECRET, SIGNED, STAMP, BODY, now=NOW - edge)
    one_more = edge + timedelta(seconds=1)
    assert not verify_signature(SECRET, SIGNED, STAMP, BODY, now=NOW + one_more)
    # A timestamp from the future is as suspect as one from the past.
    assert not verify_signature(SECRET, SIGNED, STAMP, BODY, now=NOW - one_more)


def test_the_tolerance_can_be_set():
    later = NOW + timedelta(minutes=30)
    assert not verify_signature(SECRET, SIGNED, STAMP, BODY, now=later)
    assert verify_signature(
        SECRET, SIGNED, STAMP, BODY, now=later, tolerance=timedelta(hours=1)
    )
    assert not verify_signature(
        SECRET,
        SIGNED,
        STAMP,
        BODY,
        now=NOW + timedelta(seconds=2),
        tolerance=timedelta(0),
    )
    assert verify_signature(
        SECRET, SIGNED, STAMP, BODY, now=NOW, tolerance=timedelta(0)
    )


def test_a_stale_timestamp_is_refused_even_with_a_correct_signature():
    # The replay window: a captured delivery cannot be sent again an hour later.
    assert not verify_signature(
        SECRET, SIGNED, STAMP, BODY, now=NOW + timedelta(hours=1)
    )


def test_now_may_be_a_function_and_defaults_to_the_platform_clock(monkeypatch):
    assert verify_signature(SECRET, SIGNED, STAMP, BODY, now=lambda: NOW)
    monkeypatch.setattr(clock, "now", lambda: NOW)
    assert verify_signature(SECRET, SIGNED, STAMP, BODY)
    monkeypatch.setattr(clock, "now", lambda: NOW + timedelta(hours=1))
    assert not verify_signature(SECRET, SIGNED, STAMP, BODY)


def test_the_comparison_is_constant_time(monkeypatch):
    # The decision is compare_digest's and nothing else's: forcing it either way
    # forces the result, so no ``==`` and no early exit decides in its place.
    seen: list[tuple[bytes, bytes]] = []
    answer = [False]

    def compare(a: bytes, b: bytes) -> bool:
        seen.append((a, b))
        return answer[0]

    monkeypatch.setattr(hmac, "compare_digest", compare)
    assert not verify_signature(SECRET, SIGNED, STAMP, BODY, now=NOW)
    answer[0] = True
    assert verify_signature(SECRET, "v1=" + "0" * 64, STAMP, BODY, now=NOW)
    assert all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in seen)


def test_every_entry_is_compared_even_after_one_matches(monkeypatch):
    calls: list[int] = []
    real = hmac.compare_digest

    def compare(a: bytes, b: bytes) -> bool:
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", compare)
    assert verify_signature(SECRET, f"{SIGNED},v1=aa,v1=bb", STAMP, BODY, now=NOW)
    assert len(calls) == 3


# What the docs tell a receiver to write, run as code: the snippet must work against
# what the platform sends, with nothing but the standard library.
def receiver_verifies(secret: str, headers: dict[str, str], body: bytes) -> bool:
    timestamp = headers["X-Xolotl-Timestamp"]
    if abs(time.time() - int(timestamp)) > 300:
        return False
    signed = f"{timestamp}.".encode() + body
    expected = "v1=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return any(
        hmac.compare_digest(entry.strip().encode(), expected.encode())
        for entry in headers["X-Xolotl-Signature"].split(",")
    )


def test_the_receiver_snippet_in_the_docs_accepts_what_the_platform_signs():
    stamp = int(time.time())
    headers = {
        "X-Xolotl-Timestamp": str(stamp),
        "X-Xolotl-Signature": sign(SECRET, stamp, BODY),
    }
    assert receiver_verifies(SECRET, headers, BODY)
    assert not receiver_verifies(SECRET, headers, BODY + b"x")
    assert not receiver_verifies("whsec_" + "1" * 32, headers, BODY)
    old = {
        "X-Xolotl-Timestamp": str(stamp - 301),
        "X-Xolotl-Signature": sign(SECRET, stamp - 301, BODY),
    }
    assert not receiver_verifies(SECRET, old, BODY)


# ---- the retry schedule ----------------------------------------------------------


@pytest.mark.parametrize(
    ("attempt", "minutes"),
    [(1, 2), (2, 4), (3, 8), (4, 16), (5, 32), (6, 60), (7, 60), (30, 60), (10**6, 60)],
)
def test_the_backoff_doubles_from_two_minutes_to_an_hour(attempt, minutes):
    assert backoff(attempt) == timedelta(minutes=minutes)


def test_the_backoff_of_attempt_zero_is_one_minute():
    assert backoff(0) == timedelta(minutes=1)


def test_a_negative_attempt_has_no_backoff():
    with pytest.raises(ValueError, match="attempt"):
        _ = backoff(-1)
