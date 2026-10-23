"""The SP-04 exit demo, scripted over committed fixtures (spec §19; plan 4c C11).

Two tenants: ``acme`` with the ITF pack for product ``EMONEY`` and the AVI pack for
``CRYPTO``, ``beta`` with the ITF pack for every product. Tenant A loads the Mexico
typology library through the CLI and activates three rules; its December batch is
accepted, re-sent without duplicates and processed; staff see unit amounts while
the API client sees ``PROCESSED`` only; ``CASH_STRUCTURING`` fires once, the cash
and AVI captures are written, a late crypto operation regroups the AVI
accumulation, December and Q4 close with no discrepancy, the N1 decision blocks on
the tier limit with one webhook, the OFAC counterparties wait for review and a
confirmed one fires ``COUNTERPARTY_TRUE_MATCH``, a tuned rule goes through four
eyes and the replay reproduces the stored hits. Tenant B sees none of it and both
audit chains verify. Each numbered section logs one line when it fails, so a
broken run names the step the demo stopped at.

The clock is patched: 30 June 2026 for the tenants and rules (``rules activate``
activates from today, reconciliation finding 23), 4 December for the customers and
the AVI accumulation (an operation received more than a week after it occurred is
held for a backfill, so the accumulation is sent as it happens), 14 December for the
week, 1 January 2027 for the period close and 14 December again from the decision
on (12 December for sending the late operation). Staff sign in before the clock
moves: tokens are signed on the clock but checked on the real one. The AVI pack is in
force from 30 November 2026 (plan 4b Open question 14), so the accumulation starts
that day. Plan 4c Deviation 18 lists where this test departs from C11's text.
"""

import json
import logging
import threading
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, override
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from typer.testing import CliRunner

from tests.conftest import ADMIN_PASSWORD
from tests.e2e.conftest import drain
from tests.helpers import first_login, new_tenant, system_ctx
from tests.modules.monitoring.rules.hook_support import load_units_2026
from tests.modules.monitoring.support import ITF, assign_packs, both_packs_tenant
from tests.modules.party.route_support import machine_headers, post, staff_headers
from tests.platform.jobs_support import enqueue
from tests.platform.policy_support import install_both_packs
from xolotl.app import build_app
from xolotl.cli import app as cli_app
from xolotl.modules.monitoring.captures.enums import CaptureGroupStatus
from xolotl.modules.monitoring.captures.models import CaptureGroup
from xolotl.modules.monitoring.events import REPORTABLE_EVENT_SUPERSEDED
from xolotl.modules.screening.catalog.poll import LISTS_POLL_KIND
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
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

FIXTURES = Path(__file__).parent / "fixtures" / "sp04"
LIBRARY = Path("tests/modules/screening/fixtures")
LISTS = {
    "OFAC_SDN": LIBRARY / "ofac" / "sdn_enhanced.xml",
    "UN_CONSOLIDATED": LIBRARY / "un" / "consolidated.xml",
}
# The clock of each part of the demo (module docstring).
RULES_DAY = datetime(2026, 6, 30, 15, tzinfo=UTC)
ACCUMULATION_DAY = datetime(2026, 12, 4, 18, tzinfo=UTC)
WEEK_DAY = datetime(2026, 12, 14, 15, tzinfo=UTC)
LATE_SENT = datetime(2026, 12, 12, 18, tzinfo=UTC)
CLOSE_DAY = datetime(2027, 1, 1, 11, tzinfo=UTC)

ACTIVE_CODES = ("CASH_STRUCTURING", "COUNTERPARTY_TRUE_MATCH", "TIER_N1_MONTHLY_OPS")
CASH = "CASH_STRUCTURING"
TRUE_MATCH = "COUNTERPARTY_TRUE_MATCH"
TIER_N1 = "TIER_N1_MONTHLY_OPS"
AVI_A = "AVI_XVI_A"
ROR = "ITF_ROR_CASH"
INTL = "ITF_INTL_TRANSFER"
TIER_LIMIT_REACHED = "monitoring.tier_limit_reached.v1"
DECISION_ISSUED = "monitoring.decision_issued.v1"
# Webhook event types that must never leave the platform (spec §15, D20).
STAFF_ONLY_PREFIXES = (
    "monitoring.rule_hit_",
    "monitoring.reportable_event_",
    "monitoring.capture_period_",
    "screening.counterparty_",
    "screening.match_",
)
# The API key: transactions:decide does not exist, the decide route takes
# transactions:write (plan 4c, notes from C5).
API_SCOPES = (
    "transactions:write",
    "transactions:read",
    "parties:write",
    "screening:read",
)
CLIENT_VIEW = {"id", "external_id", "occurred_at", "status", "late"}
STRUCTURING_DEPOSITS = ("cash-1208", "cash-1209", "cash-1210")
AVI_FIRST = ("avi-1130", "avi-1201", "avi-1202", "avi-1203", "avi-1204", "avi-1211")
# A's customers: (external_ref, product, tier); B's one customer.
A_CUSTOMERS = (
    ("EMONEY-001", "EMONEY", "N2"),
    ("CRYPTO-001", "CRYPTO", None),
    ("EMONEY-N1", "EMONEY", "N1"),
)
B_CUSTOMER = "B-001"
STAFF_ROLES = (Role.MLRO, Role.SENIOR_ANALYST, Role.ANALYST)
# Decisions answer within this many milliseconds on CI or fall to the fail mode;
# the service budget (80 ms) is C10's perf record, not a CI gate.
CI_DECIDE_BUDGET_MS = 1000


def _items(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@contextmanager
def section(number: int, title: str) -> Generator[None]:
    """Name the step of the demo that a failure happened in.

    Yields:
        Nothing; an exception leaves one log line naming the section and goes on.

    """
    try:
        yield
    except Exception:
        logger.exception("SP-04 exit: section %d (%s) failed", number, title)
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
def receivers() -> Iterator[tuple[Receiver, Receiver]]:
    """Serve one webhook receiver per tenant for the length of the test.

    Yields:
        Tenant A's receiver and tenant B's, listening.

    """
    served = (Receiver(), Receiver())
    for one in served:
        one.thread.start()
    yield served
    for one in served:
        one.server.shutdown()
        one.server.server_close()
        one.thread.join()


@pytest.fixture
def exit_app(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FastAPI]:
    """Build the application over an object store of this test's own.

    The CLI and the routes read the same settings, so the list files the CLI loads
    land under one root. The decide budget is widened for CI (``CI_DECIDE_BUDGET_MS``).

    Yields:
        The application.

    """
    monkeypatch.setenv("XOLOTL_OBJECT_STORE_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("XOLOTL_MONITORING_DECIDE_BUDGET_MS", str(CI_DECIDE_BUDGET_MS))
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


def _cli(*args: str) -> str:
    """Run a CLI command as the operator does and expect it to succeed.

    Returns:
        Its standard output.

    """
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == 0, (args, result.output)
    return result.stdout


def _email(tenant: CreatedTenant, role: Role) -> str:
    return f"{role.value.lower()}@{tenant.slug}.mx"


def _get(client: TestClient, headers: dict[str, str], path: str, **params: str):
    response = client.get(path, headers=headers, params=params or None)
    assert response.status_code == 200, (path, response.text)
    return response.json()


def _items_of(client: TestClient, headers: dict[str, str], path: str, **params: str):
    """Read every item of a paginated list.

    Returns:
        The items of every page.

    """
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = {**params, "limit": "200"}
        if cursor is not None:
            query["cursor"] = cursor
        page = _get(client, headers, path, **query)
        items += page["items"]
        cursor = page["next_cursor"]
        if cursor is None:
            return items


def _webhook(client: TestClient, headers: dict[str, str], url: str) -> None:
    created = post(
        client,
        headers,
        "/v1/webhooks/endpoints",
        {"url": url, "event_types": ["*"]},
        uuid4().hex,
    )
    assert created.status_code == 201, created.text


def _delivered(tenant: CreatedTenant) -> list[str]:
    """List the event type of every webhook delivery of the tenant's endpoints.

    Returns:
        One type per ``webhook_delivery`` row.

    """
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return list(
            s.scalars(
                select(OutboxRow.type).join(
                    WebhookDelivery, WebhookDelivery.event_id == OutboxRow.id
                )
            )
        )


def _outbox_count(tenant: CreatedTenant, event_type: str) -> int:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(OutboxRow)
                .where(OutboxRow.type == event_type)
            )
            or 0
        )


def _groups(tenant: CreatedTenant) -> dict[UUID, CaptureGroup]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return {row.id: row for row in s.scalars(select(CaptureGroup)).all()}


@dataclass
class Demo:
    """The handles one section of the demo hands to the ones after it."""

    client: TestClient
    services: PlatformServices
    receivers: tuple[Receiver, Receiver]
    set_clock: Callable[[datetime], None]
    acme: CreatedTenant
    beta: CreatedTenant
    a_key: dict[str, str]
    b_key: dict[str, str]
    # Staff headers by (tenant slug, role), signed in on the real clock.
    staff: dict[tuple[str, Role], dict[str, str]] = field(default_factory=dict)
    # Filled by the sections that create them.
    versions: dict[str, str] = field(default_factory=dict)
    relationships: dict[str, str] = field(default_factory=dict)
    parties: dict[str, str] = field(default_factory=dict)
    txn: dict[str, str] = field(default_factory=dict)
    b_txn: str = ""
    decision_id: str = ""
    structuring_hit: dict[str, Any] = field(default_factory=dict)
    avi_group: str = ""

    def headers(self, tenant: CreatedTenant, role: Role) -> dict[str, str]:
        return self.staff[tenant.slug, role]

    def at(self, instant: datetime) -> None:
        """Move the demo's clock."""
        self.set_clock(instant)

    def txn_id(self, external_id: str) -> str:
        """Find tenant A's transaction by external id, as staff.

        Returns:
            Its id.

        """
        if external_id not in self.txn:
            [found] = _get(
                self.client,
                self.headers(self.acme, Role.ANALYST),
                "/v1/transactions",
                external_id=external_id,
            )["items"]
            self.txn[external_id] = found["id"]
        return self.txn[external_id]


def _tenants(
    client: TestClient,
    services: PlatformServices,
    admin_engine: Engine,
    receivers: tuple[Receiver, Receiver],
    set_clock: Callable[[datetime], None],
) -> Demo:
    """Section 1: two tenants, their packs, reference data, lists, staff and keys.

    Returns:
        The demo's handles.

    """
    acme = new_tenant("acme", "admin@acme.mx")
    beta = new_tenant("beta", "admin@beta.mx")
    _ = both_packs_tenant(acme, admin_engine)
    # itf_tenant would load the reference data a second time: assign the pack.
    assign_packs(beta, [(install_both_packs(admin_engine)[ITF], None)])
    load_units_2026(admin_engine)
    demo = Demo(
        client=client,
        services=services,
        receivers=receivers,
        set_clock=set_clock,
        acme=acme,
        beta=beta,
        a_key=machine_headers(acme, *API_SCOPES),
        b_key=machine_headers(beta, *API_SCOPES),
    )
    # Tokens are signed on the clock but checked on the real one, so every member
    # signs in before the clock moves.
    for tenant, receiver in zip((acme, beta), receivers, strict=True):
        demo.staff[tenant.slug, Role.ADMIN] = first_login(
            client,
            tenant.slug,
            tenant.admin_email,
            tenant.admin_password,
            ADMIN_PASSWORD,
        )
        for role in STAFF_ROLES:
            demo.staff[tenant.slug, role] = staff_headers(
                client, tenant, email=_email(tenant, role), roles=(role,)
            )
        _webhook(client, demo.headers(tenant, Role.ADMIN), receiver.url)
    set_clock(RULES_DAY)
    assert _cli("lists", "seed")
    for code, path in LISTS.items():
        assert _cli("lists", "load-file", code, str(path)).startswith(f"{code} v1: ")
    for tenant in (acme, beta):
        _ = enqueue(services.queue, tenant.tenant_id, kind=LISTS_POLL_KIND)
    _ = drain(services)
    return demo


def _rules(demo: Demo) -> None:
    """Section 2: the library loaded, drafts seeded, three rules activated."""
    acme = demo.acme
    author = _email(acme, Role.SENIOR_ANALYST)
    approver = _email(acme, Role.MLRO)
    loaded = _cli("rules", "load")
    assert "CASH_STRUCTURING" in loaded, loaded
    seeded = _cli("rules", "seed-tenant", "--tenant", acme.slug, "--author", author)
    for line in seeded.splitlines():
        if line.startswith("draft "):
            code, version_id = line.split()[1], line.rsplit("(", 1)[1].rstrip(")")
            demo.versions[code] = version_id
    assert set(ACTIVE_CODES) <= set(demo.versions), seeded
    for code in ACTIVE_CODES:
        activated = _cli(
            "rules",
            "activate",
            "--tenant",
            acme.slug,
            "--version",
            demo.versions[code],
            "--approved-by",
            approver,
            "--simulate-from",
            "2026-06-01",
            "--simulate-to",
            "2026-06-29",
        )
        assert f"active {code} v1 from 2026-06-30" in activated, activated
    _ = drain(demo.services)
    analyst = demo.headers(acme, Role.ANALYST)
    active = _items_of(demo.client, analyst, "/v1/monitoring/rules", status="ACTIVE")
    assert sorted(rule["code"] for rule in active) == sorted(ACTIVE_CODES)


def _open(demo: Demo, tenant: CreatedTenant, ref: str, product: str | None, tier):
    writer = demo.a_key if tenant is demo.acme else demo.b_key
    party = post(
        demo.client,
        writer,
        "/v1/parties",
        {
            "party_type": "PERSON",
            "person": {"given_names": "Marisol", "first_surname": f"Cliente {ref}"},
        },
        uuid4().hex,
    )
    assert party.status_code == 201, party.text
    opened = post(
        demo.client,
        writer,
        "/v1/relationships",
        {
            "party_id": party.json()["id"],
            "external_ref": ref,
            "product": product,
            "account_tier": tier,
        },
        uuid4().hex,
    )
    assert opened.status_code == 201, opened.text
    demo.parties[ref] = party.json()["id"]
    demo.relationships[ref] = opened.json()["id"]


def _customers(demo: Demo) -> None:
    """Section 3: tenant A's three customers and tenant B's one, through the API."""
    demo.at(ACCUMULATION_DAY)
    for ref, product, tier in A_CUSTOMERS:
        _open(demo, demo.acme, ref, product, tier)
    _open(demo, demo.beta, B_CUSTOMER, None, None)
    _ = drain(demo.services)


def _send(
    demo: Demo, headers: dict[str, str], items: list[dict[str, Any]]
) -> dict[str, Any]:
    sent = post(
        demo.client, headers, "/v1/transactions", {"transactions": items}, uuid4().hex
    )
    assert sent.status_code == 202, sent.text
    return sent.json()


def _batches(demo: Demo) -> None:
    """Section 4: the batches accepted, re-sent as duplicates and processed."""
    accumulation, week = _items("batch_accumulation.json"), _items("batch_week.json")
    # The accumulation is sent as it happens, by 4 December; the week on the 14th.
    accepted = _send(demo, demo.a_key, accumulation)
    assert (accepted["accepted"], accepted["duplicates"]) == (len(accumulation), 0)
    _ = drain(demo.services)
    demo.at(WEEK_DAY)
    accepted = _send(demo, demo.a_key, week)
    assert (accepted["accepted"], accepted["duplicates"]) == (len(week), 0)
    for items in (accumulation, week):
        again = _send(demo, demo.a_key, items)
        assert (again["accepted"], again["duplicates"]) == (0, len(items)), again
    b_item = {
        **week[0],
        "external_id": "b-1209",
        "customer_ref": B_CUSTOMER,
        "account_ref": "acct-b-001",
    }
    [demo.b_txn] = _send(demo, demo.b_key, [b_item])["transaction_ids"]
    _ = drain(demo.services)
    analyst = demo.headers(demo.acme, Role.ANALYST)
    rows = _items_of(demo.client, analyst, "/v1/transactions")
    assert len(rows) == len(accumulation) + len(week)
    assert {row["status"] for row in rows} == {"PROCESSED"}, [
        (row["external_id"], row["status"]) for row in rows
    ]
    demo.txn |= {row["external_id"]: row["id"] for row in rows}


def _unit(view: dict[str, Any], key: str) -> dict[str, Any]:
    [found] = [amount for amount in view["unit_amounts"] if amount["key"] == key]
    return found


def _enrichment(demo: Demo) -> None:
    """Section 5: staff see the unit amounts; the API client sees status only."""
    client, analyst = demo.client, demo.headers(demo.acme, Role.ANALYST)
    txn_id = demo.txn_id("cash-1212")
    staff_view = _get(client, analyst, f"/v1/transactions/{txn_id}")
    usd = _unit(staff_view, "USD/PRIOR_BANKING_DAY")
    # Saturday 12 December: the prior banking day's FIX is Friday the 11th's.
    assert usd["resolved_date"] == "2026-12-11", usd
    assert usd["unit_value_id"] is not None
    # MXN 100,000 at 18.2025 is past the USD 5,000 threshold.
    assert Decimal(usd["amount"]) == Decimal("5493.750858"), usd
    udi = _unit(staff_view, "UDI/LAST_DAY_PREV_MONTH")
    assert udi["resolved_date"] == "2026-11-30", udi
    assert udi["unit_value_id"] is not None
    # UMA is the AVI pack's unit: a CRYPTO operation carries it, an EMONEY one not.
    assert "UMA/SAME_DAY" not in {
        amount["key"] for amount in staff_view["unit_amounts"]
    }
    crypto = _get(client, analyst, f"/v1/transactions/{demo.txn_id('avi-1211')}")
    uma = _unit(crypto, "UMA/SAME_DAY")
    assert (uma["resolved_date"], uma["unit_value_id"] is not None) == (
        "2026-12-11",
        True,
    )
    assert Decimal(_unit(staff_view, "MXN/SAME_DAY")["amount"]) == 100_000
    # Monday 14 December reads the same Friday FIX (plan 4c Deviation 14).
    monday = _get(client, analyst, f"/v1/transactions/{demo.txn_id('cash-1214')}")
    assert _unit(monday, "USD/PRIOR_BANKING_DAY")["resolved_date"] == "2026-12-11"
    client_view = _get(client, demo.a_key, f"/v1/transactions/{txn_id}")
    assert set(client_view) == CLIENT_VIEW, client_view
    assert client_view["status"] == "PROCESSED"


def _structuring(demo: Demo) -> None:
    """Section 6: CASH_STRUCTURING once, three deposits as evidence."""
    client, analyst = demo.client, demo.headers(demo.acme, Role.ANALYST)
    found = _items_of(client, analyst, "/v1/monitoring/hits", rule_code=CASH)
    assert len(found) == 1, found
    [hit] = found
    assert hit["relationship_id"] == demo.relationships["EMONEY-001"]
    detail = _get(client, analyst, f"/v1/monitoring/hits/{hit['id']}")
    named = {txn_id: external_id for external_id, txn_id in demo.txn.items()}
    links = {
        named[row["transaction_id"]]: row["role"] for row in detail["transactions"]
    }
    # The three near-threshold deposits are the evidence. Spec §9.6: a later
    # firing in the same dedup week links as WINDOW and counts (the Saturday
    # deposit, while the window still holds the three), and one inside the
    # cooldown links as COOLDOWN (Monday's); neither makes a second hit.
    assert links == {
        "cash-1208": "WINDOW",
        "cash-1209": "WINDOW",
        "cash-1210": "TRIGGER",
        "cash-1212": "WINDOW",
        "cash-1214": "COOLDOWN",
    }, links
    assert detail["fired_count"] == 2
    assert detail["evidence"]["aggregates"]["near_count"]["value"] == "3"
    demo.structuring_hit = detail
    for external_id in STRUCTURING_DEPOSITS:
        view = _get(client, demo.a_key, f"/v1/transactions/{demo.txn_id(external_id)}")
        assert set(view) == CLIENT_VIEW, view
        assert view["status"] == "PROCESSED"


def _events(demo: Demo, code: str) -> list[dict[str, Any]]:
    analyst = demo.headers(demo.acme, Role.ANALYST)
    return _items_of(demo.client, analyst, "/v1/monitoring/captures", capture_code=code)


def _event(demo: Demo, event_id: str) -> dict[str, Any]:
    analyst = demo.headers(demo.acme, Role.ANALYST)
    return _get(demo.client, analyst, f"/v1/monitoring/captures/{event_id}")


def _captures(demo: Demo) -> None:
    """Section 7: the one cash capture at Friday's FIX and the whole AVI group."""
    [ror] = _events(demo, ROR)
    assert ror["transaction_id"] == demo.txn_id("cash-1212")
    assert ror["period_key"] == "2026-Q4"
    converted = _event(demo, ror["id"])["unit_values"]["converted"]
    assert converted["resolved_date"] == "2026-12-11", converted
    [intl] = _events(demo, INTL)
    assert intl["transaction_id"] == demo.txn_id("swift-1209")
    assert intl["period_key"] == "2026-12"
    avi = _events(demo, AVI_A)
    assert sorted(event["transaction_id"] for event in avi) == sorted(
        demo.txn_id(external_id) for external_id in AVI_FIRST
    )
    assert len({event["capture_group_id"] for event in avi}) == 1
    assert {event["status"] for event in avi} == {"CAPTURED"}
    by_txn = {event["transaction_id"]: event for event in avi}
    assert by_txn[demo.txn_id("avi-1130")]["period_key"] == "2026-11"
    assert {by_txn[demo.txn_id(e)]["period_key"] for e in AVI_FIRST[1:]} == {"2026-12"}
    detail = _event(demo, avi[0]["id"])
    assert detail["unit_values"]["threshold"] == {"amount": "210", "unit": "UMA"}
    assert detail["group"]["status"] == "CROSSED"
    assert detail["group"]["member_count"] == len(AVI_FIRST)
    demo.avi_group = str(avi[0]["capture_group_id"])


def _late_event(demo: Demo) -> None:
    """Section 8: a late crypto operation regroups AVI and re-fires nothing."""
    before = _groups(demo.acme)[UUID(demo.avi_group)]
    superseded_before = _outbox_count(demo.acme, REPORTABLE_EVENT_SUPERSEDED)
    demo.at(LATE_SENT)
    late = _items("late_crypto.json")
    [late_id] = _send(demo, demo.a_key, late)["transaction_ids"]
    _ = drain(demo.services)
    demo.at(WEEK_DAY)
    analyst = demo.headers(demo.acme, Role.ANALYST)
    row = _get(demo.client, analyst, f"/v1/transactions/{late_id}")
    assert (row["status"], row["late"]) == ("PROCESSED", True), row
    avi = _events(demo, AVI_A)
    assert sorted(event["transaction_id"] for event in avi) == sorted([
        *(demo.txn_id(external_id) for external_id in AVI_FIRST),
        late_id,
    ])
    assert {event["status"] for event in avi} == {"CAPTURED"}
    [new_group] = {str(event["capture_group_id"]) for event in avi}
    assert new_group != demo.avi_group
    groups = _groups(demo.acme)
    old, rebuilt = groups[UUID(demo.avi_group)], groups[UUID(new_group)]
    # B10 (plan 4b Deviation 23): the group is superseded, its events captured
    # again in place, so each of the six publishes a supersession.
    assert old.status is CaptureGroupStatus.SUPERSEDED
    assert old.superseded_by_id == rebuilt.id
    assert rebuilt.status is CaptureGroupStatus.CROSSED
    assert rebuilt.accumulated_minor == before.accumulated_minor + 500_000
    assert rebuilt.threshold_crossed_at is not None
    assert before.threshold_crossed_at is not None
    assert rebuilt.threshold_crossed_at < before.threshold_crossed_at
    superseded = _outbox_count(demo.acme, REPORTABLE_EVENT_SUPERSEDED)
    assert superseded - superseded_before == len(AVI_FIRST)
    [hit] = _items_of(demo.client, analyst, "/v1/monitoring/hits", rule_code=CASH)
    assert hit["id"] == demo.structuring_hit["id"]
    assert hit["fired_count"] == demo.structuring_hit["fired_count"]


def _period_close(demo: Demo) -> None:
    """Section 9: December 2026 and Q4 2026 close with no discrepancy."""
    demo.at(CLOSE_DAY)
    closed = _cli("monitoring", "period-close", "--tenant", demo.acme.slug)
    assert "period-close: no period to close" not in closed, closed
    analyst = demo.headers(demo.acme, Role.ANALYST)
    periods = {
        (row["capture_code"], row["period_key"]): row
        for row in _items_of(demo.client, analyst, "/v1/monitoring/captures/periods")
    }
    for key in (
        (AVI_A, "2026-12"),
        (AVI_A, "2026-11"),
        (INTL, "2026-12"),
        (ROR, "2026-Q4"),
    ):
        period = periods[key]
        assert period["status"] == "CLOSED", period
        assert not any((period["discrepancies"] or {}).values()), period
        assert period["reconciled_count"] == period["event_count"], period
    assert periods[AVI_A, "2026-12"]["event_count"] == len(AVI_FIRST)
    assert periods[ROR, "2026-Q4"]["event_count"] == 1
    assert periods[INTL, "2026-12"]["event_count"] == 1
    demo.at(WEEK_DAY)


def _leaked(types: list[str]) -> list[str]:
    return sorted({
        t for t in types if t.startswith(STAFF_ONLY_PREFIXES) or t == DECISION_ISSUED
    })


def _decide(demo: Demo) -> None:
    """Section 10: the N1 decision blocks on the tier limit, one webhook only."""
    client, analyst = demo.client, demo.headers(demo.acme, Role.ANALYST)
    candidate = _items("decide_n1.json")
    response = post(
        client, demo.a_key, "/v1/transactions/decide", candidate, uuid4().hex
    )
    assert response.status_code == 200, response.text
    answer = response.json()
    logger.info("SP-04 exit: the N1 decision took %d ms", answer["latency_ms"])
    assert answer["decision"] == "BLOCK", answer
    assert answer["reasons"] == [
        {"code": "TIER_LIMIT", "tier": "N1", "limit_code": "monthly_ops"}
    ]
    assert answer["latency_ms"] < CI_DECIDE_BUDGET_MS
    demo.decision_id = answer["decision_id"]
    staff_view = _get(client, analyst, f"/v1/monitoring/decisions/{demo.decision_id}")
    assert staff_view["mode"] == "EVALUATED"
    assert {reason["rule_code"] for reason in staff_view["reasons"]} == {TIER_N1}
    client_view = _get(
        client, demo.a_key, f"/v1/monitoring/decisions/{demo.decision_id}"
    )
    assert set(client_view) == {
        "decision_id",
        "decision",
        "mode",
        "decided_at",
        "external_id",
    }, client_view
    [txn_id] = _send(demo, demo.a_key, [candidate])["transaction_ids"]
    _ = drain(demo.services)
    processed = _get(client, analyst, f"/v1/transactions/{txn_id}")
    assert processed["status"] == "PROCESSED"
    assert processed["decision_id"] == demo.decision_id
    linked = _get(client, analyst, f"/v1/monitoring/decisions/{demo.decision_id}")
    assert linked["transaction"]["id"] == txn_id
    delivered = _delivered(demo.acme)
    assert delivered.count(TIER_LIMIT_REACHED) == 1, delivered
    assert _outbox_count(demo.acme, TIER_LIMIT_REACHED) == 1
    assert not _leaked(delivered), _leaked(delivered)
    received = demo.receivers[0].received
    assert received.count(TIER_LIMIT_REACHED) == 1, received
    assert not _leaked(received)


def _finding(findings: list[dict[str, Any]], counterparty_id: str) -> dict[str, Any]:
    mine = [item for item in findings if item["counterparty_id"] == counterparty_id]
    assert mine, counterparty_id
    return mine[0]


def _dispose(demo: Demo, headers: dict[str, str], match_id: str, decision: str):
    return post(
        demo.client,
        headers,
        f"/v1/screening/matches/{match_id}/dispositions",
        {"decision": decision, "rationale": "La dirección coincide con la lista"},
        uuid4().hex,
    )


def _confirm(demo: Demo, findings: list[dict[str, Any]], counterparty_id: str):
    """Escalate each finding of the counterparty as the analyst; confirm as the MLRO."""
    analyst = demo.headers(demo.acme, Role.ANALYST)
    mlro = demo.headers(demo.acme, Role.MLRO)
    for item in findings:
        if item["counterparty_id"] != counterparty_id:
            continue
        escalated = _dispose(demo, analyst, item["id"], "POSSIBLE_TRUE_MATCH")
        assert escalated.status_code == 201, escalated.text
        assert escalated.json()["match_status"] == "ESCALATED"
        confirmed = _dispose(demo, mlro, item["id"], "TRUE_MATCH")
        assert confirmed.status_code == 201, confirmed.text
        assert confirmed.json()["match_status"] == "TRUE_MATCH"
    _ = drain(demo.services)


def _staff_only(demo: Demo) -> None:
    """Check that no hit, capture or counterparty status reaches a client (§15)."""
    for path in (
        "/v1/counterparties",
        "/v1/monitoring/hits",
        "/v1/monitoring/captures",
    ):
        refused = demo.client.get(path, headers=demo.a_key)
        assert refused.status_code == 403, (path, refused.text)


def _counterparties(demo: Demo) -> None:
    """Section 11: the OFAC counterparties wait for review; a confirmed one fires."""
    client, analyst = demo.client, demo.headers(demo.acme, Role.ANALYST)
    # The customers were screened when they were created (SP-03), like the
    # counterparties their transactions name (first sight, C1).
    for ref, _product, _tier in A_CUSTOMERS:
        screened = _get(client, analyst, f"/v1/screening/parties/{demo.parties[ref]}")
        assert screened["status"] == "CLEAR", (ref, screened)
    pending = _items_of(client, analyst, "/v1/counterparties", status="PENDING_REVIEW")
    assert len(pending) == 2, pending
    [wallet] = [row for row in pending if row["wallet_masked"] == "****mcfd"]
    [named] = [row for row in pending if row["wallet_masked"] is None]
    assert named["initials"] == "R.A.P.", named
    findings = _items_of(client, analyst, "/v1/screening/matches")
    _ = _finding(findings, named["id"])
    wallet_finding = _finding(findings, wallet["id"])
    detail = _get(client, analyst, f"/v1/screening/matches/{wallet_finding['id']}")
    rules = [rule["code"] for rule in detail["latest_hit"]["features"]["rules"]]
    assert "ID_MATCH_FLOOR" in rules, detail["latest_hit"]["features"]
    _confirm(demo, findings, wallet["id"])
    row = _get(client, analyst, f"/v1/counterparties/{wallet['id']}")
    assert row["screening_status"] == "TRUE_MATCH", row
    again = {
        **_items("batch_week.json")[-2],
        "external_id": "wallet-1214",
        "occurred_at": "2026-12-14T08:40:00-06:00",
    }
    [txn_id] = _send(demo, demo.a_key, [again])["transaction_ids"]
    _ = drain(demo.services)
    found = _items_of(client, analyst, "/v1/monitoring/hits", rule_code=TRUE_MATCH)
    assert [hit["first_transaction_id"] for hit in found] == [txn_id], found
    assert found[0]["relationship_id"] == demo.relationships["EMONEY-001"]
    assert not _leaked(_delivered(demo.acme))
    _staff_only(demo)


def _tuned(demo: Demo, rule_id: str) -> str:
    """Tune a new version as the senior analyst and simulate six months.

    Returns:
        The version's path.

    """
    client, author = demo.client, demo.headers(demo.acme, Role.SENIOR_ANALYST)
    rules = f"/v1/monitoring/rules/{rule_id}"
    created = post(client, author, f"{rules}/versions", {}, uuid4().hex)
    assert created.status_code == 201, created.text
    path = f"{rules}/versions/{created.json()['version']}"
    tuned = client.patch(path, json={"params": {"near_ratio": "0.75"}}, headers=author)
    assert tuned.status_code == 200, tuned.text
    simulated = post(
        client,
        author,
        f"{path}/simulate",
        {"range_start": "2026-06-14", "range_end": "2026-12-13"},
        uuid4().hex,
    )
    assert simulated.status_code == 202, simulated.text
    _ = drain(demo.services)
    simulation_id = simulated.json()["simulation_id"]
    simulation = _get(client, author, f"/v1/monitoring/simulations/{simulation_id}")
    assert simulation["status"] == "COMPLETED", simulation
    # The three deposits are inside the narrower band [3750, 5000) too.
    assert simulation["hit_count"] == 1, simulation
    return path


def _replay(demo: Demo) -> list[str]:
    """Replay the demo week as the operator does.

    Returns:
        The command's output lines.

    """
    replayed = runner.invoke(
        cli_app,
        [
            "monitoring",
            "replay",
            "--tenant",
            demo.acme.slug,
            "--from",
            "2026-12-07",
            "--to",
            "2026-12-13",
            "--dry-run",
        ],
    )
    assert replayed.exit_code == 0, replayed.output
    return replayed.stdout.splitlines()


def _governance(demo: Demo) -> None:
    """Section 12: a tuned CASH_STRUCTURING through four eyes; the replay agrees."""
    client = demo.client
    author = demo.headers(demo.acme, Role.SENIOR_ANALYST)
    mlro = demo.headers(demo.acme, Role.MLRO)
    [rule] = [
        rule
        for rule in _items_of(client, author, "/v1/monitoring/rules")
        if rule["code"] == CASH
    ]
    path = _tuned(demo, rule["id"])
    submitted = post(client, author, f"{path}/submit", {}, uuid4().hex)
    assert submitted.status_code == 200, submitted.text
    refused = post(client, author, f"{path}/approve", {}, uuid4().hex)
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "RULE_TRANSITION_NOT_ALLOWED"
    assert "SAME_AUTHOR" in str(refused.json()["blocked_reasons"]), refused.text
    approved = post(client, mlro, f"{path}/approve", {}, uuid4().hex)
    assert approved.status_code == 200, approved.text
    activated = post(
        client,
        mlro,
        f"{path}/activate",
        {"effective_from": "2026-12-14", "product": None},
        uuid4().hex,
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "ACTIVE"
    # The new version starts on the 14th, after the week: v1 is replayed.
    lines = _replay(demo)
    assert lines[-1] == "identical", lines
    assert f"{CASH} v1: simulated 1, stored 1, missing 0, extra 0" in lines, lines
    # The effectiveness metrics name rules and count hits: staff only (C6). A
    # hit belongs to the month it was created in, on the database's clock.
    period = str(demo.structuring_hit["created_at"])[:7]
    metrics = _get(client, mlro, "/v1/monitoring/metrics", period=period)
    by_code = {row["rule_code"]: row["hits"] for row in metrics["rules"]}
    assert by_code.get(CASH) == 1, metrics["rules"]
    refused = client.get(
        "/v1/monitoring/metrics", params={"period": period}, headers=demo.a_key
    )
    assert refused.status_code == 403, refused.text


def _tenant_b_sees_nothing(demo: Demo) -> None:
    """Section 13: nothing of tenant A reaches tenant B."""
    client, b_analyst = demo.client, demo.headers(demo.beta, Role.ANALYST)
    rows = _items_of(client, b_analyst, "/v1/transactions")
    assert [row["id"] for row in rows] == [demo.b_txn]
    for path in (
        "/v1/monitoring/hits",
        "/v1/monitoring/captures",
        "/v1/counterparties",
    ):
        assert _items_of(client, b_analyst, path) == [], path
    for path in (
        f"/v1/transactions/{demo.txn_id('cash-1212')}",
        f"/v1/monitoring/decisions/{demo.decision_id}",
    ):
        foreign = client.get(path, headers=b_analyst)
        assert foreign.status_code == 404, (path, foreign.text)
    screened = _get(
        client, b_analyst, f"/v1/screening/parties/{demo.parties[B_CUSTOMER]}"
    )
    assert screened["status"] == "CLEAR", screened
    assert TIER_LIMIT_REACHED not in _delivered(demo.beta)
    assert TIER_LIMIT_REACHED not in demo.receivers[1].received
    assert not _leaked(_delivered(demo.beta))


def _audit(demo: Demo) -> None:
    """Section 14: both tenants' audit chains verify."""
    for tenant in (demo.acme, demo.beta):
        verified = _get(
            demo.client, demo.headers(tenant, Role.ADMIN), "/v1/audit/verify"
        )
        assert verified["valid"] is True, verified
        assert verified["entries_checked"] > 0


# One demo, fourteen numbered sections: the plan fixes it as a single test.
@pytest.mark.usefixtures("_restore_logging")
def test_sp04_exit(
    exit_app: FastAPI,
    exit_client: TestClient,
    admin_engine: Engine,
    receivers: tuple[Receiver, Receiver],
    monkeypatch: pytest.MonkeyPatch,
):
    def set_clock(instant: datetime) -> None:
        monkeypatch.setattr(clock, "now", lambda: instant)

    services = _services(exit_app)
    with section(1, "two tenants"):
        demo = _tenants(exit_client, services, admin_engine, receivers, set_clock)
    with section(2, "rules"):
        _rules(demo)
    with section(3, "customers"):
        _customers(demo)
    with section(4, "batches"):
        _batches(demo)
    with section(5, "enrichment"):
        _enrichment(demo)
    with section(6, "structuring"):
        _structuring(demo)
    with section(7, "captures"):
        _captures(demo)
    with section(8, "late event"):
        _late_event(demo)
    with section(9, "period close"):
        _period_close(demo)
    with section(10, "decide"):
        _decide(demo)
    with section(11, "counterparties"):
        _counterparties(demo)
    with section(12, "governance and replay"):
        _governance(demo)
    with section(13, "tenant B sees nothing"):
        _tenant_b_sees_nothing(demo)
    with section(14, "audit"):
        _audit(demo)
