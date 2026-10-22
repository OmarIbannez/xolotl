"""Rule templates in the database: the typology library as shared pack content.

``xolotl rules load`` installs the library's files into ``rule_template`` and
``rule_template_version`` as the database owner (spec §5.1). A template version is
immutable: loading the same ``(code, version)`` with another definition hash
raises ``RuleTemplateImmutableError`` before anything of the batch is written
(plan 4b Deviation 2). Reads run in any session; the application role has
``SELECT`` on both tables.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.errors import (
    RuleTemplateImmutableError,
    RuleTemplateUnknownError,
)
from xolotl.modules.monitoring.rules.definitions import RuleDefinition
from xolotl.modules.monitoring.rules.library import TypologyFile
from xolotl.modules.monitoring.rules.models import RuleTemplate, RuleTemplateVersion


@dataclass(frozen=True, slots=True)
class LoadReport:
    """What a load did, as ``"CODE v<n>"`` per template version."""

    created: tuple[str, ...]
    unchanged: tuple[str, ...]


def _label(file: TypologyFile) -> str:
    return f"{file.code} v{file.version}"


def _template_values(file: TypologyFile) -> dict[str, object]:
    definition = file.definition
    return {
        "family": definition.family.value,
        "kind": definition.kind.value,
        "title": definition.title,
        "typology": list(definition.typology),
        "country_code": file.country_code,
        "packs": list(file.packs),
    }


def _latest_files(
    files: Sequence[TypologyFile], loaded: dict[str, int]
) -> list[TypologyFile]:
    # The highest version of each code in the batch, unless the database already
    # holds a higher one: the template row follows its latest version only.
    latest: dict[str, TypologyFile] = {}
    for file in files:
        current = latest.get(file.code)
        if current is None or file.version > current.version:
            latest[file.code] = file
    return [
        file
        for file in latest.values()
        if file.version >= loaded.get(file.code, file.version)
    ]


def load_templates(
    s: Session, files: Sequence[TypologyFile], *, loaded_by: str
) -> LoadReport:
    """Install template versions that are new; check the others are unchanged.

    Runs in an owner-role session. ``rule_template`` takes its family, kind, title,
    typology, country and packs from the latest version of each code.

    Returns:
        The versions created and those already loaded with the same hash.

    Raises:
        RuleTemplateImmutableError: When a loaded ``(code, version)`` comes with
            another definition hash; nothing of the batch is written.

    """
    existing = {
        (row.template_code, row.version): row.definition_hash
        for row in s.scalars(select(RuleTemplateVersion))
    }
    created: list[TypologyFile] = []
    unchanged: list[str] = []
    for file in files:
        digest = existing.get((file.code, file.version))
        if digest is None:
            created.append(file)
        elif bytes(digest) == file.definition_hash:
            unchanged.append(_label(file))
        else:
            msg = f"{_label(file)} is loaded with another definition"
            raise RuleTemplateImmutableError(msg)
    loaded: dict[str, int] = {}
    for key in existing:
        code, version = key
        loaded[code] = max(version, loaded.get(code, version))
    for file in _latest_files(files, loaded):
        values = _template_values(file)
        statement = insert(RuleTemplate).values(code=file.code, **values)
        _ = s.execute(
            statement.on_conflict_do_update(
                index_elements=[RuleTemplate.code], set_=values
            )
        )
    for file in created:
        s.add(
            RuleTemplateVersion(
                template_code=file.code,
                version=file.version,
                definition=file.definition.model_dump(mode="json"),
                definition_hash=file.definition_hash,
                loaded_by=loaded_by,
                source_path=file.source_path,
            )
        )
    s.flush()
    return LoadReport(
        created=tuple(_label(file) for file in created), unchanged=tuple(unchanged)
    )


def template_version(
    s: Session, code: str, version: int | None = None
) -> RuleTemplateVersion:
    """Find a template version, the latest one when ``version`` is None.

    Returns:
        The version row.

    Raises:
        RuleTemplateUnknownError: When no such template or version is loaded.

    """
    query = select(RuleTemplateVersion).where(RuleTemplateVersion.template_code == code)
    if version is not None:
        query = query.where(RuleTemplateVersion.version == version)
    row = s.scalars(
        query.order_by(RuleTemplateVersion.version.desc()).limit(1)
    ).one_or_none()
    if row is None:
        shown = code if version is None else f"{code} v{version}"
        msg = f"no rule template {shown}"
        raise RuleTemplateUnknownError(msg)
    return row


def latest_template_versions(
    s: Session, *, packs: Collection[str] | None = None
) -> list[RuleTemplateVersion]:
    """List the latest version of every template, optionally for some packs only.

    Returns:
        One version per code, sorted by code; with ``packs``, only the templates
        that declare at least one of them.

    """
    if packs is not None and not packs:
        return []
    latest = (
        select(
            RuleTemplateVersion.template_code,
            func.max(RuleTemplateVersion.version).label("version"),
        )
        .group_by(RuleTemplateVersion.template_code)
        .subquery()
    )
    query = (
        select(RuleTemplateVersion)
        .join(
            latest,
            (latest.c.template_code == RuleTemplateVersion.template_code)
            & (latest.c.version == RuleTemplateVersion.version),
        )
        .join(RuleTemplate, RuleTemplate.code == RuleTemplateVersion.template_code)
        .order_by(RuleTemplateVersion.template_code)
    )
    if packs is not None:
        query = query.where(
            or_(*(RuleTemplate.packs.contains([pack]) for pack in sorted(packs)))
        )
    return list(s.scalars(query))


def definition_of(row: RuleTemplateVersion) -> RuleDefinition:
    """Read a template version's stored definition back into its type.

    Returns:
        The validated definition.

    """
    return RuleDefinition.model_validate(row.definition)
