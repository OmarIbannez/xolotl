"""A delivery over a real TLS connection, sent to the address that was checked (§9.2).

The other delivery tests mock the receiver, so they see the request the application
builds but not what the HTTP client does with it. These start a TLS server on the
loopback with a certificate for a name that does not resolve anywhere, and let the
application deliver to it for real: the name is resolved by the policy's resolver, the
request goes to the address that came back, and the certificate is still checked against
the name. Loopback is not public, so ``_is_public`` is told otherwise for these tests
alone, and the client trusts the server's certificate and no other.
"""

import datetime as dt
import http.server
import ssl
import threading
from collections.abc import Generator
from pathlib import Path
from typing import override
from uuid import UUID

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from sqlalchemy import Engine

from tests.helpers import make_tenant_with_keys
from tests.platform.jobs_support import FakeClock
from tests.platform.webhooks_support import (
    Resolves,
    Stack,
    an_event,
    delivery_for,
    make_endpoint,
)
from xolotl.platform.enums import DeliveryStatus
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.events.webhooks import verify_signature

NAME = "hooks.example.test"
OTHER_NAME = "other.example.test"


class Receiver:
    """A TLS server on the loopback, with a certificate for one name.

    It keeps the path, the body and the headers of every request it is sent.
    """

    def __init__(self, name: str, directory: Path) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        now = dt.datetime.now(dt.UTC)
        certificate = (
            x509
            .CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False
            )
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None), critical=True
            )
            .sign(key, hashes.SHA256())
        )
        self.certificate: Path = directory / "receiver.pem"
        _ = self.certificate.write_bytes(
            certificate.public_bytes(serialization.Encoding.PEM)
        )
        private = directory / "receiver.key"
        _ = private.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self.requests: list[tuple[str, bytes, dict[str, str]]] = []
        received = self.requests

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                size = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(size)
                received.append((self.path, body, dict(self.headers.items())))
                self.send_response(200)
                self.send_header("content-length", "0")
                self.end_headers()

            @override
            def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.certificate, private)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.port: int = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@pytest.fixture
def tenant_id(engine: Engine) -> UUID:
    return make_tenant_with_keys("alpha")


@pytest.fixture
def receiver(tmp_path: Path) -> Generator[Receiver]:
    (directory := tmp_path / "receiver").mkdir()
    started = Receiver(NAME, directory)
    yield started
    started.stop()


@pytest.fixture
def impostor(tmp_path: Path) -> Generator[Receiver]:
    (directory := tmp_path / "impostor").mkdir()
    started = Receiver(OTHER_NAME, directory)
    yield started
    started.stop()


@pytest.fixture
def stack(engine: Engine) -> Generator[Stack]:
    reset_subscribers()
    yield Stack(FakeClock(), resolver=Resolves({NAME: ["127.0.0.1"]}))
    reset_subscribers()


def trust(monkeypatch: pytest.MonkeyPatch, certificate: Path) -> None:
    """Let deliveries trust one certificate and call the loopback public.

    Nothing else about the policy or the client is switched off.
    """

    def everything_is_public(_address: object) -> bool:
        return True

    monkeypatch.setattr(
        "xolotl.platform.events.url_policy._is_public", everything_is_public
    )
    context = ssl.create_default_context(cafile=str(certificate))

    class TrustingClient(httpx.Client):
        def __init__(
            self, *, timeout: httpx.Timeout, follow_redirects: bool, trust_env: bool
        ) -> None:
            super().__init__(
                timeout=timeout,
                follow_redirects=follow_redirects,
                trust_env=trust_env,
                verify=context,
            )

    monkeypatch.setattr(httpx, "Client", TrustingClient)


def test_a_delivery_reaches_the_address_the_name_resolved_to_and_checks_the_name(
    stack: Stack,
    receiver: Receiver,
    tenant_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    # The name resolves nowhere but in the policy's resolver, so the connection can only
    # have been made to the address it returned.
    trust(monkeypatch, receiver.certificate)
    hook = make_endpoint(tenant_id, url=f"https://{NAME}:{receiver.port}/xolotl?x=1")
    event = an_event(tenant_id, type_="party.created.v1")
    assert stack.fan_out(tenant_id, event) == 1

    assert stack.run_jobs() == 1

    ((path, body, headers),) = receiver.requests
    assert path == "/xolotl?x=1"
    assert headers["Host"] == f"{NAME}:{receiver.port}"
    assert headers["X-Xolotl-Event-Id"] == str(event.id)
    assert verify_signature(
        hook.secret,
        headers["X-Xolotl-Signature"],
        headers["X-Xolotl-Timestamp"],
        body,
        now=stack.clock,
    )
    row = delivery_for(tenant_id, event, hook)
    assert (row.status, row.response_code) == (DeliveryStatus.DELIVERED, 200)


def test_a_certificate_for_another_name_is_refused_and_nothing_is_sent(
    stack: Stack,
    impostor: Receiver,
    tenant_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    # The server answers on the address the name resolved to, and its certificate is
    # one the client trusts, but it is for another name: whoever is there is not who
    # the URL names.
    trust(monkeypatch, impostor.certificate)
    hook = make_endpoint(tenant_id, url=f"https://{NAME}:{impostor.port}/xolotl")
    event = an_event(tenant_id)
    assert stack.fan_out(tenant_id, event) == 1

    assert stack.run_jobs() == 1

    assert impostor.requests == []
    row = delivery_for(tenant_id, event, hook)
    assert row.status is DeliveryStatus.PENDING
    assert row.response_code is None
    assert row.last_error is not None
    assert row.last_error.startswith("ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED]")
    assert "Hostname mismatch" in row.last_error
