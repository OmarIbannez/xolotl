"""The HTTP list fetcher, against ``respx``: validators, redirects, caps, errors."""

import hashlib
from collections.abc import Iterator

import httpx
import pytest
import respx

from xolotl.modules.screening.catalog.fetcher import (
    FetchResult,
    HttpListFetcher,
    ListFetchError,
)

URL = "https://lists.test/consolidated.xml"
BODY = b"<CONSOLIDATED_LIST>" + b"x" * 100 + b"</CONSOLIDATED_LIST>"
SECRET_BODY = b"<html>Internal error for Jane Q. Listed, RFC XAXX010101000</html>"


def _fetcher(max_bytes: int = 1_000) -> HttpListFetcher:
    return HttpListFetcher(timeout_seconds=5, max_bytes=max_bytes)


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


def test_a_200_returns_the_body_its_hash_and_validators(mock: respx.MockRouter):
    _ = mock.get(URL).respond(
        200,
        content=BODY,
        headers={"ETag": '"abc"', "Last-Modified": "Mon, 19 Oct 2026 12:00:00 GMT"},
    )
    result = _fetcher().fetch(URL, etag=None, last_modified=None)
    assert result == FetchResult(
        not_modified=False,
        body=BODY,
        sha256=hashlib.sha256(BODY).digest(),
        etag='"abc"',
        last_modified="Mon, 19 Oct 2026 12:00:00 GMT",
    )


def test_conditional_headers_are_sent_and_304_is_not_modified(
    mock: respx.MockRouter,
):
    route = mock.get(URL).respond(304)
    result = _fetcher().fetch(
        URL, etag='"abc"', last_modified="Mon, 19 Oct 2026 12:00:00 GMT"
    )
    assert result == FetchResult(
        not_modified=True,
        body=None,
        sha256=None,
        etag='"abc"',
        last_modified="Mon, 19 Oct 2026 12:00:00 GMT",
    )
    sent = route.calls.last.request.headers
    assert sent["If-None-Match"] == '"abc"'
    assert sent["If-Modified-Since"] == "Mon, 19 Oct 2026 12:00:00 GMT"


def test_no_conditional_headers_without_validators(mock: respx.MockRouter):
    route = mock.get(URL).respond(200, content=BODY)
    result = _fetcher().fetch(URL, etag=None, last_modified=None)
    sent = route.calls.last.request.headers
    assert "If-None-Match" not in sent
    assert "If-Modified-Since" not in sent
    assert (result.etag, result.last_modified) == (None, None)


def test_redirects_are_followed(mock: respx.MockRouter):
    moved = "https://cdn.lists.test/consolidated.xml"
    _ = mock.get(URL).respond(302, headers={"Location": moved})
    _ = mock.get(moved).respond(200, content=BODY)
    result = _fetcher().fetch(URL, etag=None, last_modified=None)
    assert result.body == BODY


@pytest.mark.parametrize("length", ["declared", "undeclared"])
def test_a_body_over_the_cap_is_too_large(mock: respx.MockRouter, length: str):
    big = b"y" * 2_000
    if length == "declared":
        _ = mock.get(URL).respond(200, content=big)
    else:
        # A streamed body with no Content-Length: the cap counts what arrives.
        _ = mock.get(URL).mock(
            return_value=httpx.Response(200, stream=httpx.ByteStream(big))
        )
    with pytest.raises(ListFetchError) as caught:
        _ = _fetcher(max_bytes=1_000).fetch(URL, etag=None, last_modified=None)
    assert caught.value.code == "TOO_LARGE"
    assert str(caught.value).startswith("TOO_LARGE: ")


def test_a_body_at_the_cap_is_accepted(mock: respx.MockRouter):
    _ = mock.get(URL).respond(200, content=b"z" * 1_000)
    result = _fetcher(max_bytes=1_000).fetch(URL, etag=None, last_modified=None)
    assert result.body == b"z" * 1_000


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (httpx.Response(403), "HTTP_403"),
        (httpx.Response(500), "HTTP_500"),
        (httpx.Response(503), "HTTP_503"),
        (httpx.ReadTimeout("slow"), "TIMEOUT"),
        (httpx.ConnectError("refused"), "NETWORK"),
    ],
)
def test_error_statuses_timeouts_and_network_errors_have_codes(
    mock: respx.MockRouter, answer: httpx.Response | Exception, code: str
):
    route = mock.get(URL)
    if isinstance(answer, httpx.Response):
        _ = route.mock(return_value=answer)
    else:
        _ = route.mock(side_effect=answer)
    with pytest.raises(ListFetchError) as caught:
        _ = _fetcher().fetch(URL, etag=None, last_modified=None)
    assert caught.value.code == code
    assert str(caught.value).startswith(f"{code}: ")


def test_an_error_message_never_carries_the_body(mock: respx.MockRouter):
    _ = mock.get(URL).respond(503, content=SECRET_BODY)
    with pytest.raises(ListFetchError) as caught:
        _ = _fetcher().fetch(URL, etag=None, last_modified=None)
    message = str(caught.value)
    assert message.startswith("HTTP_503: ")
    assert "Jane" not in message
    assert "XAXX" not in message
    assert "html" not in message.lower()


def test_a_given_client_is_used(mock: respx.MockRouter):
    _ = mock.get(URL).respond(200, content=BODY)
    with httpx.Client() as client:
        fetcher = HttpListFetcher(timeout_seconds=5, max_bytes=1_000, client=client)
        assert fetcher.fetch(URL, etag=None, last_modified=None).body == BODY
        assert not client.is_closed  # the caller's client is the caller's to close
