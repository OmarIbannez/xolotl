"""Load pack files, hash them over canonical JSON and install immutable versions.

A pack file lives at ``<packs_dir>/<CC>/<SECTOR>-<LICENCE>/v<N>.yaml``; directories
whose name starts with ``_`` hold units, holidays and other reference data, not
packs. The content hash is the SHA-256 of the canonical JSON of the validated
config (sorted keys, no whitespace, Decimals and dates as strings: program spec
addendum b), so a key reorder or another spelling of the same amount hashes the
same, and an installed version can never change under the same number.
"""

import hashlib
from pathlib import Path
from typing import cast
from uuid import UUID

import yaml
from pydantic import JsonValue, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.platform.audit import canonical_json
from xolotl.platform.policy.errors import PackImmutableError, PackValidationError
from xolotl.platform.policy.models import PolicyPack, PolicyPackVersion
from xolotl.platform.policy.schema import PackConfig, unverified_paths
from xolotl.platform.sqltypes import contains_float
from xolotl.platform.units.errors import UnverifiedValueError


def pack_files(packs_dir: Path) -> list[Path]:
    """List the pack files under ``packs_dir``.

    Returns:
        Every ``<CC>/<PACK>/v*.yaml`` in sorted order, skipping any path through a
        directory whose name starts with ``_``.

    """
    return sorted(
        path
        for path in packs_dir.glob("*/*/v*.yaml")
        if not any(
            part.startswith("_") for part in path.relative_to(packs_dir).parts[:-1]
        )
    )


def load_pack(path: Path, *, allow_unverified: bool = False) -> PackConfig:
    """Read and validate one pack file.

    Returns:
        The validated config.

    Raises:
        PackValidationError: When the file cannot be read, is not a YAML mapping
            or does not follow the schema.
        UnverifiedValueError: When the pack holds ``UNVERIFIED`` values and
            ``allow_unverified`` is false; the detail names every one.

    """
    try:
        raw = cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        msg = f"{path.name}: not a pack"
        raise PackValidationError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"{path.name}: not a pack"
        raise PackValidationError(msg)
    try:
        config = PackConfig.model_validate(raw)
    except ValidationError as exc:
        msg = f"{path.name}: {exc.error_count()} validation errors"
        raise PackValidationError(msg) from exc
    paths = unverified_paths(config)
    if paths and not allow_unverified:
        msg = f"{config.pack} v{config.version}: {', '.join(paths)}"
        raise UnverifiedValueError(msg)
    return config


def _config_json(config: PackConfig) -> dict[str, JsonValue]:
    dump = cast("dict[str, JsonValue]", config.model_dump(mode="json"))
    if contains_float(dump):
        # Every number of the schema is a PlainDecimal, an int or a bool; a float
        # would not survive JSONB, and the stored config would not hash back.
        msg = f"{config.pack} v{config.version} dumps a float"
        raise ValueError(msg)
    return dump


def content_hash(config: PackConfig) -> bytes:
    """Hash a pack over its canonical JSON.

    Returns:
        The 32-byte SHA-256 digest.

    """
    return hashlib.sha256(canonical_json(_config_json(config))).digest()


def install_pack(
    session: Session, config: PackConfig, *, source_path: str, loaded_by: str
) -> UUID:
    """Install one pack version; needs an owner session (the app role only reads).

    The pack row is created on first sight, its code split into country, sector
    and licence; its title follows the newest version installed. A version already
    installed with the same hash is left as it is.

    Returns:
        The id of the ``policy_pack_version`` row.

    Raises:
        PackImmutableError: When the version is installed with another hash.

    """
    digest = content_hash(config)
    pack = session.scalar(select(PolicyPack).where(PolicyPack.code == config.pack))
    if pack is None:
        country, rest = config.pack.split("/")
        sector, licence = rest.split("-")
        pack = PolicyPack(
            code=config.pack,
            country_code=country,
            sector=sector,
            licence=licence,
            title=config.title,
        )
        session.add(pack)
        session.flush()
    existing = session.scalar(
        select(PolicyPackVersion).where(
            PolicyPackVersion.pack_id == pack.id,
            PolicyPackVersion.version == config.version,
        )
    )
    if existing is not None:
        if existing.content_hash != digest:
            msg = (
                f"{config.pack} v{config.version} is installed with another content "
                "hash; bump the version"
            )
            raise PackImmutableError(msg)
        return existing.id
    version = PolicyPackVersion(
        pack_id=pack.id,
        version=config.version,
        effective_from=config.effective_from,
        effective_to=config.effective_to,
        config=_config_json(config),
        content_hash=digest,
        loaded_by=loaded_by,
        source_path=source_path,
    )
    pack.title = config.title
    session.add(version)
    session.flush()
    return version.id
