"""The SP-03 exit demo, scripted over committed fixtures (spec §16; plan 3c F8).

Two tenants on the ITF pack. The five shared lists are loaded from the library's
samples with ``xolotl lists load-file`` and tenant A uploads its own ``MX_LPB``.
Tenant A's parties are screened as they are created, an explained ``ALERT`` reaches
staff while an API client sees status only, the UN list's second version re-screens
only what it changed, tenant B sees nothing of tenant A, the four-eyes review path
closes a finding, no screening finding ever reaches a webhook, and both audit chains
verify. Each numbered section logs one line when it fails, so a broken run names the
step the demo stopped at.
"""

import logging
import threading
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, cast, override
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from typer.testing import CliRunner

from tests.conftest import ADMIN_PASSWORD
from tests.e2e.conftest import drain
from tests.helpers import first_login, new_tenant, system_ctx
from tests.modules.party.route_support import machine_headers, post, staff_headers
from tests.modules.screening.catalog.support import UN_V2, assign_tenant_wide
from tests.platform.jobs_support import enqueue
from tests.platform.policy_support import install_both_packs
from xolotl.app import build_app
from xolotl.cli import app as cli_app
from xolotl.modules.party.enums import PartyType
from xolotl.modules.party.schemas import PartyCreateRequest, PersonInput
from xolotl.modules.screening.catalog.poll import LISTS_POLL_KIND
from xolotl.modules.screening.catalog.template import CSV_TYPE
from xolotl.modules.screening.engine.enums import RunMode
from xolotl.modules.screening.engine.models import ScreeningRun, ScreeningSubject
from xolotl.modules.screening.matching import MATCHER_VERSION
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.config import get_settings
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.events.models import OutboxRow, WebhookDelivery
from xolotl.platform.events.webhooks import EVENT_TYPE_HEADER
from xolotl.platform.services import PlatformServices

if TYPE_CHECKING:
    from xolotl.platform.api.app import AppState

logger = logging.getLogger(__name__)
runner = CliRunner()

LIBRARY = Path("tests/modules/screening/fixtures")
# The library's trimmed samples, by list code (each folder's README names the source).
FIXTURES = {
    "UN_CONSOLIDATED": LIBRARY / "un" / "consolidated.xml",
    "OFAC_SDN": LIBRARY / "ofac" / "sdn_enhanced.xml",
    "EU_FSF": LIBRARY / "eu" / "full_sanctions_list_1_1.xml",
    "UK_FCDO": LIBRARY / "uk" / "UK-Sanctions-List.xml",
    "MX_SAT_69B": LIBRARY / "sat69b" / "Listado_Completo_69-B.csv",
}
UN = "UN_CONSOLIDATED"
OFAC = "OFAC_SDN"
LPB = "MX_LPB"
LPB_FILE = Path(__file__).parent / "fixtures" / "lpb_tenant_a.csv"
LPB_ROWS = 4
SCREENING = "/v1/screening"
API_SCOPES = ("screening:write", "screening:read", "parties:write")
# Webhook event types that must never leave the platform (spec §11).
STAFF_ONLY_PREFIXES = ("screening.match_", "private_list.", "screening.true_match")
STATUS_CHANGED = "screening.status_changed.v1"


def _person(
    given: str,
    first: str,
    second: str | None = None,
    born: date | None = None,
) -> dict[str, object]:
    body = PartyCreateRequest(
        party_type=PartyType.PERSON,
        person=PersonInput(
            given_names=given,
            first_surname=first,
            second_surname=second,
            birth_date=born,
        ),
    )
    return body.model_dump(mode="json")


# Tenant A's parties: perturbed list names (P_LPB and P_OFAC drop the second
# surname; P_LPB also loses its accents), and one that resembles nobody.
P_LPB = _person("Angela Beatriz", "Verastegui", born=date(1982, 4, 17))
P_OFAC = _person("Rogelio", "Nieto", born=date(1978, 3, 13))
P_CHG = _person("Ali Hassan", "Al-Majid", "Al-Tikriti", date(1943, 1, 1))
P_REM = _person("Saddam", "Hussein", "Al-Tikriti", date(1937, 4, 28))
P_ADD = _person("Zephyrino", "Qualmsby", born=date(1970, 1, 1))
P_CLEAR = _person("Citlali", "Xochitl")
# Tenant B's party: row LPB-A-004 of tenant A's file, to the letter.
B_TWIN = _person("Rutilio", "Cuauhtémoc", "Zempoalteca", date(1968, 11, 2))


@contextmanager
def section(number: int, title: str) -> Generator[None]:
    """Name the step of the demo that a failure happened in.

    Yields:
        Nothing; an exception leaves one log line naming the section and goes on.

    """
    try:
        yield
    except Exception:
        logger.exception("SP-03 exit: section %d (%s) failed", number, title)
        raise


@pytest.fixture
def _restore_logging() -> Generator[None]:
    # The CLI's configure_logging leaves a stream handler bound to a CliRunner
    # stream that is closed once the test ends.
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


class Receiver:
    """A webhook receiver on loopback that answers 200 and keeps the event types."""

    def __init__(self) -> None:
        self.received: list[str] = []
        received = self.received

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                _ = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                received.append(self.headers.get(EVENT_TYPE_HEADER, ""))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            @override
            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/hooks"


@pytest.fixture
def receiver() -> Iterator[Receiver]:
    """Serve a webhook receiver for the length of the test.

    Yields:
        The receiver, listening.

    """
    served = Receiver()
    served.thread.start()
    yield served
    served.server.shutdown()
    served.server.server_close()
    served.thread.join()


@pytest.fixture
def exit_app(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FastAPI]:
    """Build the application over an object store of this test's own.

    The CLI and the routes read the same settings, so the list files the CLI loads
    and the LPB upload land under one root.

    Yields:
        The application.

    """
    monkeypatch.setenv("XOLOTL_OBJECT_STORE_ROOT", str(tmp_path / "objects"))
    get_settings.cache_clear()
    yield build_app()
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.fixture
def exit_client(exit_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(exit_app) as c:
        yield c


def _services(app: FastAPI) -> PlatformServices:
    state = cast("AppState", app.state.xolotl)
    assert state.services is not None
    return state.services


def _load(code: str, path: Path) -> str:
    """Ingest a list file through the CLI, as the operator does.

    Returns:
        The command's one output line.

    """
    result = runner.invoke(cli_app, ["lists", "load-file", code, str(path)])
    assert result.exit_code == 0, result.output
    return result.stdout.strip()


def _staff(client: TestClient, tenant: CreatedTenant, role: Role) -> dict[str, str]:
    email = f"{role.value.lower()}@{tenant.slug}.mx"
    return staff_headers(client, tenant, email=email, roles=(role,))


def _admin(client: TestClient, tenant: CreatedTenant) -> dict[str, str]:
    return first_login(
        client, tenant.slug, tenant.admin_email, tenant.admin_password, ADMIN_PASSWORD
    )


def _get(client: TestClient, headers: dict[str, str], path: str, **params: str):
    response = client.get(path, headers=headers, params=params or None)
    assert response.status_code == 200, response.text
    return response.json()


def _create_party(
    client: TestClient, headers: dict[str, str], body: dict[str, object]
) -> str:
    created = post(client, headers, "/v1/parties", body, uuid4().hex)
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _check(client: TestClient, headers: dict[str, str], party_id: str) -> dict:
    checked = post(
        client, headers, f"{SCREENING}/checks", {"party_id": party_id}, uuid4().hex
    )
    assert checked.status_code == 201, checked.text
    return checked.json()


def _dispose(client: TestClient, headers: dict[str, str], match_id: str, decision):
    return post(
        client,
        headers,
        f"{SCREENING}/matches/{match_id}/dispositions",
        {"decision": decision, "rationale": "Coincide nombre y fecha de nacimiento"},
        uuid4().hex,
    )


def _findings(client: TestClient, headers: dict[str, str]) -> dict[str, dict]:
    """Read every finding of the caller's tenant.

    Returns:
        The match summaries by id.

    """
    page = _get(client, headers, f"{SCREENING}/matches", limit="200")
    assert page["next_cursor"] is None
    return {item["id"]: item for item in page["items"]}


def _party_findings(client: TestClient, headers: dict[str, str], party_id: str):
    return _get(client, headers, f"{SCREENING}/parties/{party_id}")["findings"]


def _delta_screened(tenant: CreatedTenant) -> set[str]:
    """Which parties of the tenant a ``LIST_DELTA`` run screened.

    Returns:
        Their ids.

    """
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        rows = s.scalars(
            select(ScreeningSubject.party_id)
            .join(ScreeningRun, ScreeningRun.subject_id == ScreeningSubject.id)
            .where(ScreeningRun.mode == RunMode.LIST_DELTA)
        ).all()
    return {str(party_id) for party_id in rows if party_id is not None}


def _delivered_types(tenant: CreatedTenant) -> set[str]:
    """Which event types the tenant's endpoints were handed.

    Returns:
        The types of every event that has a ``webhook_delivery`` row.

    """
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return set(
            s.scalars(
                select(OutboxRow.type).join(
                    WebhookDelivery, WebhookDelivery.event_id == OutboxRow.id
                )
            )
        )


def _webhook(client: TestClient, headers: dict[str, str], url: str) -> None:
    created = post(
        client,
        headers,
        "/v1/webhooks/endpoints",
        {"url": url, "event_types": ["*"]},
        uuid4().hex,
    )
    assert created.status_code == 201, created.text


def _only(items: list[dict], **where: object) -> dict:
    """Pick the one item whose fields equal ``where``.

    Returns:
        That item.

    """
    [item] = [i for i in items if all(i[k] == v for k, v in where.items())]
    return item


@dataclass
class Demo:
    """The handles one section of the demo hands to the ones after it."""

    client: TestClient
    services: PlatformServices
    receiver: Receiver
    acme: CreatedTenant
    beta: CreatedTenant
    a_admin: dict[str, str]
    b_admin: dict[str, str]
    a_mlro: dict[str, str]
    b_mlro: dict[str, str]
    a_analyst: dict[str, str]
    b_analyst: dict[str, str]
    a_key: dict[str, str]
    # Filled by the sections that create them.
    p_lpb: str = ""
    p_ofac: str = ""
    p_chg: str = ""
    p_rem: str = ""
    p_add: str = ""
    p_clear: str = ""
    b_twin: str = ""
    a_run: str = ""


def _tenants(
    client: TestClient,
    services: PlatformServices,
    admin_engine: Engine,
    receiver: Receiver,
) -> Demo:
    """Section 1: two tenants on the ITF pack, their staff, keys and endpoints.

    Returns:
        The demo's handles.

    """
    acme = new_tenant("acme", "admin@acme.mx")
    beta = new_tenant("beta", "admin@beta.mx")
    itf = install_both_packs(admin_engine)["MX/ITF-IFPE"]
    for tenant in (acme, beta):
        _ = assign_tenant_wide(tenant, itf, date(2026, 1, 1))
    demo = Demo(
        client=client,
        services=services,
        receiver=receiver,
        acme=acme,
        beta=beta,
        a_admin=_admin(client, acme),
        b_admin=_admin(client, beta),
        a_mlro=_staff(client, acme, Role.MLRO),
        b_mlro=_staff(client, beta, Role.MLRO),
        a_analyst=_staff(client, acme, Role.ANALYST),
        b_analyst=_staff(client, beta, Role.ANALYST),
        a_key=machine_headers(acme, *API_SCOPES),
    )
    for tenant in (acme, beta):
        _ = _staff(client, tenant, Role.SENIOR_ANALYST)
    _ = machine_headers(beta, *API_SCOPES)
    _webhook(client, demo.a_admin, receiver.url)
    _webhook(client, demo.b_admin, receiver.url)
    return demo


def _poll_both(demo: Demo) -> None:
    for tenant in (demo.acme, demo.beta):
        _ = enqueue(demo.services.queue, tenant.tenant_id, kind=LISTS_POLL_KIND)
    _ = drain(demo.services)


def _shared_lists(demo: Demo) -> None:
    """Section 2: the five shared lists, loaded once and seen by both tenants."""
    assert runner.invoke(cli_app, ["lists", "seed"]).exit_code == 0
    for code, path in FIXTURES.items():
        assert _load(code, path).startswith(f"{code} v1: ")
    _poll_both(demo)
    for headers in (demo.a_admin, demo.b_admin):
        shown = {
            item["code"]: item
            for item in _get(demo.client, headers, "/v1/lists")["items"]
        }
        assert set(shown) == set(FIXTURES), sorted(shown)
        for code in FIXTURES:
            assert (shown[code]["stale"], shown[code]["active_seq"]) == (False, 1)


def _lpb(demo: Demo) -> None:
    """Section 3: tenant A's MLRO creates the LPB and uploads its file."""
    client = demo.client
    created = post(
        client,
        demo.a_mlro,
        "/v1/private-lists",
        {"list_code": LPB, "title": "Lista de personas bloqueadas"},
        uuid4().hex,
    )
    assert created.status_code == 201, created.text
    lpb_id = created.json()["id"]
    uploaded = client.post(
        f"/v1/private-lists/{lpb_id}/versions",
        content=LPB_FILE.read_bytes(),
        headers={**demo.a_mlro, "Content-Type": CSV_TYPE, "Idempotency-Key": "lpb-1"},
    )
    assert uploaded.status_code == 201, uploaded.text
    _ = drain(demo.services)
    [listed] = _get(client, demo.a_mlro, "/v1/private-lists")["items"]
    assert listed["list_code"] == LPB
    assert listed["active_version"]["entry_count"] == LPB_ROWS


def _parties(demo: Demo) -> None:
    """Section 4: tenant A's six parties and tenant B's twin, screened on create."""
    client, a_analyst = demo.client, demo.a_analyst
    demo.p_lpb = _create_party(client, a_analyst, P_LPB)
    demo.p_ofac = _create_party(client, a_analyst, P_OFAC)
    demo.p_chg = _create_party(client, a_analyst, P_CHG)
    demo.p_rem = _create_party(client, a_analyst, P_REM)
    demo.p_add = _create_party(client, a_analyst, P_ADD)
    demo.p_clear = _create_party(client, a_analyst, P_CLEAR)
    demo.b_twin = _create_party(client, demo.b_analyst, B_TWIN)
    _ = drain(demo.services)


def _explained_hit(demo: Demo) -> None:
    """Section 5: staff see the explained ALERT; an API client sees status only."""
    client = demo.client
    staff_view = _check(client, demo.a_analyst, demo.p_ofac)
    assert staff_view["status"] == "PENDING_REVIEW"
    hit = _only(staff_view["hits"], list_code=OFAC, band="ALERT")
    features = hit["features"]
    assert features["alignment"], features
    assert all(pair["query"] and pair["entry"] for pair in features["alignment"]), (
        features["alignment"]
    )
    assert features["qualifiers"], features
    assert features["entry_ref"]["list_version_seq"] == 1
    assert features["matcher_version"] == MATCHER_VERSION
    client_view = _check(client, demo.a_key, demo.p_ofac)
    assert set(client_view) == {"run_id", "subject_id", "status", "screened_at"}
    assert client_view["status"] == "PENDING_REVIEW"
    demo.a_run = staff_view["run_id"]
    lpb_view = _check(client, demo.a_analyst, demo.p_lpb)
    _ = _only(lpb_view["hits"], list_code=LPB, band="ALERT")


def _delta(demo: Demo) -> None:
    """Section 6: the UN's second version re-screens only what it changed."""
    client, a_analyst = demo.client, demo.a_analyst
    before = _findings(client, a_analyst)
    chg_before = _only(list(before.values()), party_id=demo.p_chg, list_code=UN)
    rem_before = _only(list(before.values()), party_id=demo.p_rem, list_code=UN)
    assert _load(UN, UN_V2).startswith(f"{UN} v2: ")
    _poll_both(demo)
    batches = _get(client, a_analyst, f"{SCREENING}/batches", kind="LIST_DELTA")
    [delta_batch] = batches["items"]
    assert delta_batch["status"] == "COMPLETED", delta_batch
    after = _findings(client, a_analyst)
    new = [after[match_id] for match_id in set(after) - set(before)]
    assert sorted((m["party_id"], m["list_code"]) for m in new) == sorted([
        (demo.p_add, UN),
        (demo.p_chg, UN),
    ])
    assert after[chg_before["id"]]["status"] == "SUPERSEDED"
    assert after[rem_before["id"]]["suggested_disposition"] == "DELISTED"
    screened = _delta_screened(demo.acme)
    assert {demo.p_add, demo.p_chg} <= screened
    assert screened.isdisjoint({demo.p_lpb, demo.p_ofac, demo.p_clear})


def _tenant_b_sees_nothing(demo: Demo) -> None:
    """Section 7: nothing of tenant A reaches tenant B."""
    client, b_analyst = demo.client, demo.b_analyst
    assert _get(client, demo.b_mlro, "/v1/private-lists")["items"] == []
    assert _get(client, b_analyst, f"{SCREENING}/matches", list_code=LPB) == {
        "items": [],
        "next_cursor": None,
    }
    foreign = client.get(f"{SCREENING}/runs/{demo.a_run}", headers=b_analyst)
    assert foreign.status_code == 404, foreign.text
    twin = _get(client, b_analyst, f"{SCREENING}/parties/{demo.b_twin}")
    assert twin["status"] == "CLEAR", twin
    assert twin["findings"] == []
    a_batches = {
        b["id"] for b in _get(client, demo.a_analyst, f"{SCREENING}/batches")["items"]
    }
    b_batches = {
        b["id"] for b in _get(client, b_analyst, f"{SCREENING}/batches")["items"]
    }
    assert b_batches
    assert b_batches.isdisjoint(a_batches)


def _four_eyes(demo: Demo) -> None:
    """Section 8: the analyst escalates, cannot confirm, and the MLRO confirms."""
    client, a_analyst = demo.client, demo.a_analyst
    finding = _only(_party_findings(client, a_analyst, demo.p_lpb), list_code=LPB)
    assert finding["status"] == "OPEN"
    escalated = _dispose(client, a_analyst, finding["id"], "POSSIBLE_TRUE_MATCH")
    assert escalated.status_code == 201, escalated.text
    assert escalated.json()["match_status"] == "ESCALATED"
    refused = _dispose(client, a_analyst, finding["id"], "TRUE_MATCH")
    assert refused.status_code == 409, refused.text
    assert "SAME_REVIEWER" in refused.json()["blocked_reasons"]
    confirmed = _dispose(client, demo.a_mlro, finding["id"], "TRUE_MATCH")
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["match_status"] == "TRUE_MATCH"
    _ = drain(demo.services)


def _no_tipping_off(demo: Demo) -> None:
    """Section 9: no screening finding left on a webhook; the endpoint was live."""
    received = demo.receiver.received
    a_types, b_types = _delivered_types(demo.acme), _delivered_types(demo.beta)
    leaked = {t for t in a_types | b_types if t.startswith(STAFF_ONLY_PREFIXES)}
    assert not leaked, sorted(leaked)
    assert STATUS_CHANGED in a_types, sorted(a_types)
    assert STATUS_CHANGED in received, sorted(set(received))
    assert not {t for t in received if t.startswith(STAFF_ONLY_PREFIXES)}


def _audit(demo: Demo) -> None:
    """Section 10: both tenants' audit chains verify."""
    for headers in (demo.a_admin, demo.b_admin):
        verified = _get(demo.client, headers, "/v1/audit/verify")
        assert verified["valid"] is True, verified
        assert verified["entries_checked"] > 0


# One demo, ten numbered sections: the plan fixes it as a single test.
@pytest.mark.usefixtures("_restore_logging")
def test_sp03_exit(
    exit_app: FastAPI,
    exit_client: TestClient,
    admin_engine: Engine,
    receiver: Receiver,
):
    with section(1, "two tenants"):
        demo = _tenants(exit_client, _services(exit_app), admin_engine, receiver)
    with section(2, "shared lists"):
        _shared_lists(demo)
    with section(3, "the LPB"):
        _lpb(demo)
    with section(4, "parties"):
        _parties(demo)
    with section(5, "an explained hit"):
        _explained_hit(demo)
    with section(6, "the UN delta"):
        _delta(demo)
    with section(7, "tenant B sees nothing"):
        _tenant_b_sees_nothing(demo)
    with section(8, "four eyes"):
        _four_eyes(demo)
    with section(9, "no tipping-off"):
        _no_tipping_off(demo)
    with section(10, "audit"):
        _audit(demo)
