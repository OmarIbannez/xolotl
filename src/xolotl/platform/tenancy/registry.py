"""Tables that are not tenant-isolated (program spec D1).

Tables listed here have no RLS and are not filtered by tenant. They may reference
tenants, as ``api_key_lookup.tenant_id`` does, but the isolation harness asserts
that they carry no row security, and that every table not listed is fully
isolated. Add a table here only when it is genuinely shared across tenants
(reference data, lookups that must work before a tenant context exists).
"""

SHARED_TABLES: frozenset[str] = frozenset({"alembic_version", "api_key_lookup"})

# Shared tables the application role may also write; the rest are read-only.
SHARED_WRITABLE: frozenset[str] = frozenset({"api_key_lookup"})

# Tenant tables the application role may read and insert into but never update or
# delete; their migrations pass ``grant=APPEND_ONLY_PRIVILEGES`` to ``enable_rls``,
# and the isolation harness holds the database's grants to this list.
APPEND_ONLY_TABLES: frozenset[str] = frozenset({"audit_event"})
