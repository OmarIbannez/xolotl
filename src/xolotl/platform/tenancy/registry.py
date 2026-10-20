"""Tables that are not tenant-isolated (program spec D1).

Tables listed here have no RLS and are not filtered by tenant. They may reference
tenants, as ``api_key_lookup.tenant_id`` does, but the isolation harness asserts
that they carry no row security, and that every table not listed is fully
isolated. Add a table here only when it is genuinely shared across tenants
(reference data, lookups that must work before a tenant context exists).
"""

SHARED_TABLES: frozenset[str] = frozenset({
    "alembic_version",
    "api_key_lookup",
    "holiday",
    "list_entry",
    "list_entry_delta",
    "list_source",
    "list_version",
    "policy_pack",
    "policy_pack_version",
    "unit_value",
})

# Shared tables the application role may also write; the rest are read-only.
# ``unit_value`` is not listed: its migration grants INSERT and UPDATE (the fetch job
# appends values and marks corrections) but never DELETE. Nor are the four list
# tables: migration 0009 grants column-level rights (the operational columns of
# ``list_source``, ``list_version.status``, ``list_entry.to_seq``) and INSERT.
SHARED_WRITABLE: frozenset[str] = frozenset({"api_key_lookup"})

# Tenant tables the application role may read and insert into but never update or
# delete; their migrations pass ``grant=APPEND_ONLY_PRIVILEGES`` to ``enable_rls``,
# and the isolation harness holds the database's grants to this list.
APPEND_ONLY_TABLES: frozenset[str] = frozenset({"audit_event", "match_disposition"})
