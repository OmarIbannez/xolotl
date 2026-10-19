"""The platform's default route table and the router the composition root builds.

No per-tenant connector configuration exists yet, so every tenant routes through
``DEFAULT_ROUTES``. The adapters here are the only code that reads plan 2a's unit
names: the Banxico connector itself knows only ``(date, value)`` pairs.
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import date
from decimal import Decimal

from pydantic import SecretStr

from xolotl.platform.config import Settings
from xolotl.platform.connectors.banxico import (
    BANXICO_SIE,
    BanxicoSieConnector,
    ObservationFetch,
    ObservationPairs,
    SeedLookup,
)
from xolotl.platform.connectors.base import ConnectorDeps
from xolotl.platform.connectors.router import ConnectorRouter
from xolotl.platform.connectors.sat import SAT_RFC, SatRfcConnector
from xolotl.platform.connectors.vault import CredentialVault
from xolotl.platform.enums import CheckType, ConnectorMode
from xolotl.platform.storage import ObjectStore
from xolotl.platform.units import BanxicoSieSource, SeedFileSource, UnitValue

DEFAULT_ROUTES: Mapping[tuple[CheckType, str], tuple[str, ...]] = {
    (CheckType.UNIT_VALUE, "MX"): (BANXICO_SIE,),
    (CheckType.RFC_VALIDATION, "MX"): (SAT_RFC,),
}


def rows_to_pairs(rows: Iterable[UnitValue]) -> list[tuple[date, Decimal]]:
    """Reduce plan 2a's unit rows to the connector's pairs.

    Returns:
        ``(valid_from, value)`` for each row, in the rows' order.

    """
    return [(row.valid_from, row.value) for row in rows]


def sie_fetch_factory(settings: Settings) -> Callable[[str], ObservationFetch]:
    """Build the factory that gives the connector an SIE fetch per token.

    Returns:
        A callable from a token to a fetch over plan 2a's ``BanxicoSieSource``.

    """

    def factory(token: str) -> ObservationFetch:
        source = BanxicoSieSource(
            SecretStr(token),
            seed_dir=settings.packs_dir / "_units",
            env=settings.env,
        )

        def fetch(unit_code: str, start: date, end: date) -> ObservationPairs:
            return rows_to_pairs(source.fetch(unit_code, start, end))

        return fetch

    return factory


def seed_lookup_from(source: SeedFileSource) -> SeedLookup:
    """Read one day's value from plan 2a's committed seed files.

    A missing or malformed seed file raises plan 2a's ``ValidationFailedError``,
    which ``Connector.run`` reports as an ``ERROR`` outcome.

    Returns:
        A lookup giving the seed's value whose ``valid_from`` is the day, or
        ``None`` when the seed has no row for it.

    """

    def lookup(unit_code: str, on: date) -> Decimal | None:
        rows = source.fetch(unit_code, on, on)
        return rows[0].value if rows else None

    return lookup


def default_router(settings: Settings, *, store: ObjectStore) -> ConnectorRouter:
    """Build the process's router over the platform's connectors.

    Returns:
        A router on ``DEFAULT_ROUTES`` whose default mode is
        ``XOLOTL_CONNECTOR_MODE_DEFAULT``.

    """
    deps = ConnectorDeps(vault=CredentialVault(), store=store)
    token = (
        settings.banxico_token.get_secret_value()
        if settings.banxico_token is not None
        else None
    )
    # The files BanxicoSieSource's demo mode and the units page read.
    seeds = seed_lookup_from(
        SeedFileSource(settings.packs_dir / "_units", allow_unverified=True)
    )
    return ConnectorRouter(
        [
            BanxicoSieConnector(
                deps,
                fetch_factory=sie_fetch_factory(settings),
                seed_lookup=seeds,
                platform_token=token,
                prod=settings.env == "prod",
            ),
            SatRfcConnector(deps),
        ],
        DEFAULT_ROUTES,
        default_mode=ConnectorMode(settings.connector_mode_default),
    )
