"""The party name index: each party's names as normalized tokens (program spec D7).

Reverse screening (plan 3c) blocks on these rows in Postgres. They hold the
library's normalized tokens and phonetic keys only, never a name as written, and no
route serializes them. One row per name kind: ``LEGAL`` (a person's full name or an
organization's legal name) and ``TRADE`` when the organization has a trade name.
"""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.party.enums import PartyStatus, PartyType
from xolotl.modules.party.service import PartyScreeningProfile, screening_profile
from xolotl.modules.screening.engine.enums import NameKind
from xolotl.modules.screening.engine.models import PartyNameIndex
from xolotl.modules.screening.lists import EntityType
from xolotl.modules.screening.matching import (
    MATCHER_VERSION,
    NormalizedName,
    TokenRole,
    normalize_name,
)
from xolotl.platform import clock
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7

# Particles and legal forms say nothing about who a party is.
_UNKEYED_ROLES = frozenset({TokenRole.PARTICLE, TokenRole.LEGAL_FORM})


def _names(profile: PartyScreeningProfile) -> dict[NameKind, NormalizedName]:
    names: dict[NameKind, NormalizedName] = {}
    if profile.status is PartyStatus.PURGED:
        return names
    if profile.party_type is PartyType.PERSON:
        full = " ".join(
            p
            for p in (
                profile.given_names,
                profile.first_surname,
                profile.second_surname,
            )
            if p
        )
        surname = " ".join(
            p for p in (profile.first_surname, profile.second_surname) if p
        )
        if full:
            names[NameKind.LEGAL] = normalize_name(
                full,
                entity_type=EntityType.PERSON,
                given=profile.given_names,
                surname=surname or None,
            )
    else:
        if profile.legal_name:
            names[NameKind.LEGAL] = normalize_name(
                profile.legal_name, entity_type=EntityType.ORGANIZATION
            )
        if profile.trade_name:
            names[NameKind.TRADE] = normalize_name(
                profile.trade_name, entity_type=EntityType.ORGANIZATION
            )
    return {kind: name for kind, name in names.items() if name.tokens}


def index_party_names(s: Session, party_id: UUID) -> list[PartyNameIndex]:
    """Upsert a party's name index rows and delete those of names it lost.

    A purged party, or a party without a name, keeps no row.

    Returns:
        The party's rows after the call, ``LEGAL`` first.

    """
    tenant_id = bound_context(s).require_tenant_id()
    names = _names(screening_profile(s, party_id))
    _ = s.execute(
        delete(PartyNameIndex).where(
            PartyNameIndex.party_id == party_id,
            PartyNameIndex.name_kind.not_in(list(names)),
        )
    )
    for kind, name in names.items():
        values = {
            "norm_name": name.folded,
            "tokens": [t.folded for t in name.tokens],
            "phonetic_keys": sorted({
                t.phonetic for t in name.tokens if t.role not in _UNKEYED_ROLES
            }),
            "matcher_version": MATCHER_VERSION,
        }
        _ = s.execute(
            insert(PartyNameIndex)
            .values(
                tenant_id=tenant_id,
                id=uuid7(),
                party_id=party_id,
                name_kind=kind,
                **values,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "party_id", "name_kind"],
                set_={**values, "updated_at": clock.now()},
            )
        )
    return list(
        s.scalars(
            select(PartyNameIndex)
            .where(PartyNameIndex.party_id == party_id)
            .order_by(PartyNameIndex.name_kind)
            .execution_options(populate_existing=True)
        )
    )
