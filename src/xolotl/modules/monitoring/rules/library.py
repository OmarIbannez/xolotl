"""The Mexico typology library: one YAML file per rule template (spec §9.3).

A file is ``{version, country_code, packs, definition}`` where ``definition`` is a
``RuleDefinition`` document (plan 4b Deviation 14); the template's code, family,
kind, title and typology are the definition's. ``xolotl rules validate`` reads the
library without a database; ``xolotl rules load`` (B5) installs it.

Pure: no database, no web framework (contract ``monitoring-evaluator-is-pure``).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar, Self, cast

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    model_validator,
)

from xolotl.modules.monitoring.rules.definitions import RuleDefinition, RuleFamily
from xolotl.platform.errors import ValidationFailedError

TYPOLOGIES_DIR = Path("policy-packs/MX/_typologies")
# A pack code is the country, a slash and the pack (``MX/ITF-IFPE``).
_PACK_CODE = r"^[A-Z]{2}/[A-Z0-9-]+$"


class _TypologyDocument(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    version: StrictInt = Field(ge=1)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    packs: list[Annotated[str, Field(pattern=_PACK_CODE)]] = Field(min_length=1)
    definition: RuleDefinition

    @model_validator(mode="after")
    def _packs_are_the_countrys(self) -> Self:
        if len(set(self.packs)) != len(self.packs):
            msg = "packs repeats a pack"
            raise ValueError(msg)
        foreign = [
            pack for pack in self.packs if pack.split("/", 1)[0] != self.country_code
        ]
        if foreign:
            msg = f"pack {foreign[0]} is not of country {self.country_code}"
            raise ValueError(msg)
        return self


@dataclass(frozen=True, slots=True)
class TypologyFile:
    """One template version as read from its file."""

    code: str
    version: int
    country_code: str
    packs: tuple[str, ...]
    definition: RuleDefinition
    definition_hash: bytes
    source_path: str


def _named(path: Path, problem: str) -> str:
    return f"{path.name}: {problem}"


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error["loc"])
    message = error["msg"].removeprefix("Value error, ")
    return f"{where}: {message}" if where else message


def read_typology(path: Path) -> TypologyFile:
    """Read and validate one typology file.

    YAML 1.1 reads a bare ``on`` key as ``true``, so the files quote it
    (``"on":``); a document with a non-string key is refused like any other.

    Returns:
        The template version, with its definition hash.

    Raises:
        ValidationFailedError: Naming the file, when it cannot be read, is not a
            mapping, fails the definition's validation, is not an ``ALERT``
            template or its code differs from the file name.

    """
    try:
        raw = cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationFailedError(_named(path, "not a typology file")) from exc
    if not isinstance(raw, dict):
        raise ValidationFailedError(_named(path, "not a typology file"))
    try:
        document = _TypologyDocument.model_validate(raw)
    except ValidationError as exc:
        raise ValidationFailedError(_named(path, _first_error(exc))) from exc
    definition = document.definition
    if definition.code != path.stem:
        problem = f"the code {definition.code} differs from the file name"
        raise ValidationFailedError(_named(path, problem))
    if definition.family is not RuleFamily.ALERT:
        raise ValidationFailedError(
            _named(path, f"a template is an ALERT rule, not {definition.family}")
        )
    return TypologyFile(
        code=definition.code,
        version=document.version,
        country_code=document.country_code,
        packs=tuple(document.packs),
        definition=definition,
        definition_hash=definition.definition_hash(),
        source_path=str(path),
    )


def read_library(directory: Path = TYPOLOGIES_DIR) -> list[TypologyFile]:
    """Read every ``*.yaml`` of ``directory``.

    A code equals its file name, so no two files can repeat one.

    Returns:
        The templates sorted by code.

    """
    return sorted(
        (read_typology(path) for path in directory.glob("*.yaml")),
        key=lambda template: template.code,
    )
