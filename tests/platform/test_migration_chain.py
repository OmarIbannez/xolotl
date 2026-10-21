"""The migration revisions form one linear chain with a single head.

Plans 2b and 2c append their revision to ``CHAIN``.
"""

from alembic.script import ScriptDirectory

from xolotl.platform import migrate

CHAIN = [
    "0001_kernel",
    "0002_events_jobs_workflows",
    "0003_tenant_key",
    "0004_encrypt_totp",
    "0005_encrypt_idempotency",
    "0006_policy_units_calendar",
    "0007_connectors",
    "0008_party",
    "0009_lists",
    "0010_screening",
    "0011_screening_hit_run_index",
    "0012_transactions",
    "0013_rules_captures",
]


def test_the_revisions_form_one_chain_with_one_head():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    assert script.get_heads() == [CHAIN[-1]]
    assert [rev.revision for rev in script.walk_revisions()] == list(reversed(CHAIN))
