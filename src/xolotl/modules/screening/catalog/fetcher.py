"""Fetching a shared list's file over HTTP (spec §6.2 step 2).

``ListFetcher`` is the port the poll job calls outside any transaction;
``HttpListFetcher`` is its ``httpx`` adapter: conditional request validators,
redirects followed, a timeout, a size cap enforced while the body streams, and the
raw bytes hashed. Every failure is a ``ListFetchError`` whose message is a code and
a short reason, never the response body (it lands in ``list_source.last_error``).
"""

import hashlib
from dataclasses import dataclass
from typing import Protocol, cast

import httpx

NOT_MODIFIED = 304

# The codes of a failed fetch besides HTTP_<status>.
TIMEOUT = "TIMEOUT"
TOO_LARGE = "TOO_LARGE"
NETWORK = "NETWORK"


@dataclass(frozen=True, slots=True)
class FetchResult:
    """What a fetch brought: nothing new (304), or a body with its validators."""

    not_modified: bool
    body: bytes | None
    sha256: bytes | None
    etag: str | None
    last_modified: str | None


class ListFetchError(Exception):
    """A fetch that failed; ``code`` is HTTP_<status>, TIMEOUT, TOO_LARGE or NETWORK."""

    def __init__(self, code: str, reason: str) -> None:
        """Keep the code; the message is ``"<code>: <reason>"``."""
        super().__init__(f"{code}: {reason}")
        self.code: str = code


class ListFetcher(Protocol):
    def fetch(
        self, url: str, *, etag: str | None, last_modified: str | None
    ) -> FetchResult: ...


class HttpListFetcher:
    """``ListFetcher`` over ``httpx``, streaming the body under a size cap."""

    def __init__(
        self,
        *,
        timeout_seconds: int,
        max_bytes: int,
        client: httpx.Client | None = None,
    ) -> None:
        """Fetch with this timeout and cap, through ``client`` when one is given."""
        self._timeout: httpx.Timeout = httpx.Timeout(timeout_seconds)
        self._max_bytes: int = max_bytes
        self._client: httpx.Client | None = client

    def fetch(
        self, url: str, *, etag: str | None, last_modified: str | None
    ) -> FetchResult:
        """GET the file, conditionally when validators are given.

        Returns:
            ``not_modified`` with the given validators on a 304; otherwise the body,
            its SHA-256 and the response's ``ETag`` and ``Last-Modified``.

        Raises:
            ListFetchError: On a non-2xx status, a timeout, a network failure or a
                body over the cap.

        """
        headers: dict[str, str] = {}
        if etag is not None:
            headers["If-None-Match"] = etag
        if last_modified is not None:
            headers["If-Modified-Since"] = last_modified
        try:
            if self._client is not None:
                return self._get(self._client, url, headers, etag, last_modified)
            with httpx.Client() as client:
                return self._get(client, url, headers, etag, last_modified)
        except httpx.TimeoutException as exc:
            reason = f"no answer within {self._timeout.read:g} s"
            raise ListFetchError(TIMEOUT, reason) from exc
        except httpx.HTTPError as exc:
            raise ListFetchError(NETWORK, type(exc).__name__) from exc

    def _get(
        self,
        client: httpx.Client,
        url: str,
        headers: dict[str, str],
        etag: str | None,
        last_modified: str | None,
    ) -> FetchResult:
        with client.stream(
            "GET", url, headers=headers, follow_redirects=True, timeout=self._timeout
        ) as response:
            status = response.status_code
            if status == NOT_MODIFIED:
                return FetchResult(
                    not_modified=True,
                    body=None,
                    sha256=None,
                    etag=etag,
                    last_modified=last_modified,
                )
            if not response.is_success:
                code = f"HTTP_{status}"
                raise ListFetchError(code, f"the server answered {status}")
            body = self._read_capped(response)
            return FetchResult(
                not_modified=False,
                body=body,
                sha256=hashlib.sha256(body).digest(),
                etag=cast("str | None", response.headers.get("ETag")),
                last_modified=cast("str | None", response.headers.get("Last-Modified")),
            )

    def _read_capped(self, response: httpx.Response) -> bytes:
        too_large = f"the file is over {self._max_bytes} bytes"
        declared = cast("str", response.headers.get("Content-Length", ""))
        if declared.isdigit() and int(declared) > self._max_bytes:
            raise ListFetchError(TOO_LARGE, too_large)
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > self._max_bytes:
                raise ListFetchError(TOO_LARGE, too_large)
            chunks.append(chunk)
        return b"".join(chunks)
