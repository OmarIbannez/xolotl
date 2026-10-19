"""Per-tenant hash-chained audit log (spec §8.3, program spec D10).

Each row stores the SHA-256 of its own content plus the previous row's hash for
the same tenant, and ``seq`` counts up from 1 without gaps. A Postgres trigger
rejects UPDATE, DELETE and TRUNCATE outright, and the application role holds only
SELECT and INSERT; the chain catches what gets past them, such as a superuser who
disables the trigger. Writers serialise per tenant with a transaction-scoped
advisory lock so ``seq`` is gap-free under concurrency.

What ``verify_chain`` detects: a row whose content was edited, a row removed from
or inserted into the middle of the chain, and a broken ``prev_hash`` link. What it
cannot detect on its own: rows removed from the end (tail truncation), or a suffix
rewritten and re-hashed from some row onwards, because the chain's head is not
anchored anywhere outside the database. Anchoring it (a signed or write-once
checkpoint of the latest ``(seq, hash)``) is a later requirement.
"""

from xolotl.platform.audit.chain import (
    GENESIS_HASH,
    AuditEvent,
    ChainVerification,
    canonical_json,
    digest,
    record,
    verify_chain,
)

__all__ = [
    "GENESIS_HASH",
    "AuditEvent",
    "ChainVerification",
    "canonical_json",
    "digest",
    "record",
    "verify_chain",
]
