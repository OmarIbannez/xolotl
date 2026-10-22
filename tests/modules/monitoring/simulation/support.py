"""Builders of the simulation tests: a library rule loaded as the job loads one."""

from typing import Any
from uuid import UUID

from tests.modules.monitoring.rules.conftest import lists
from xolotl.modules.monitoring.rules.assignments import LoadedRule
from xolotl.modules.monitoring.rules.definitions import resolve_definition
from xolotl.modules.monitoring.rules.library import TYPOLOGIES_DIR, read_typology


def library_rule(code: str, **changes: Any) -> LoadedRule:
    """Load the library's template ``code`` with its default params.

    ``changes`` replace top-level fields of the definition (``dedup``, say).

    Returns:
        The rule, its list names resolved against the shipped lists.

    """
    template = read_typology(TYPOLOGIES_DIR / f"{code}.yaml")
    definition = template.definition.model_copy(update=changes)
    return LoadedRule(
        rule_id=UUID(int=10),
        rule_version_id=UUID(int=11),
        rule_code=code,
        assignment_id=UUID(int=0),
        definition=resolve_definition(definition, lists()),
        segment=definition.segment,
        sync=definition.sync,
        definition_hash=definition.definition_hash(),
        typology=tuple(definition.typology),
    )
