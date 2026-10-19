"""encrypt idempotency_key.response_body

Revision ID: 0005_encrypt_idempotency
Revises: 0004_encrypt_totp
Create Date: 2026-10-19

``response_body`` kept the first response of every ``POST`` for 24 hours, so a lost
response could be replayed, as JSONB in the clear: a new API key and a new staff
member's initial password among it. It becomes ``bytea`` holding the ciphertext of
``EncryptedJson`` (``version || nonce || AES-256-GCM`` under the tenant's DATA key, the
tenant and purpose as authenticated data), the same layout as every other encrypted
column.

Upgrade empties the table first and carries nothing over. The rows are a replay cache
that expires after 24 hours, and a client that retries a request across the upgrade
only runs it again instead of receiving the stored response: harmless. Sealing them
instead would need every tenant's keys and a tenant context for each row, for a cache
that is gone by tomorrow, and any row it missed would stay in the clear. The column is
then converted with ``USING NULL``: if a row were somehow left, the conversion would
fail on the column's NOT NULL instead of keeping its plaintext as ``bytea``.

The table is emptied with ``TRUNCATE`` and not ``DELETE`` because row-level security is
forced on it, so it binds the owner of a managed server (who is neither a superuser nor
``BYPASSRLS``): a ``DELETE`` that names no tenant finds no row there and would leave
every secret in place for the conversion to meet. ``TRUNCATE`` is not subject to row
security, takes the table's exclusive lock (no request can add a row between the
emptying and the conversion), and gives the table fresh storage, so no dead row keeps
a secret in a heap page. The conversion rewrites the table as well. Backups and
archived WAL taken before the upgrade still hold the rows until their retention ends.

Downgrade mirrors it: ciphertext cannot become JSON, so the rows go and the column is
JSONB again, for the code that predates this revision. Neither direction is safe with
the application running: the code of one side writes a type that the other side's
column refuses. Stop the application, migrate, start the new image.

The index on ``(tenant_id, expires_at)``, the primary key, the policy and the grants are
untouched by both.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_encrypt_idempotency"
down_revision: str | None = "0004_encrypt_totp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("TRUNCATE TABLE idempotency_key")
    op.alter_column(
        "idempotency_key",
        "response_body",
        existing_type=postgresql.JSONB(),
        type_=sa.LargeBinary(),
        existing_nullable=False,
        postgresql_using="NULL::bytea",
    )


def downgrade() -> None:
    op.execute("TRUNCATE TABLE idempotency_key")
    op.alter_column(
        "idempotency_key",
        "response_body",
        existing_type=sa.LargeBinary(),
        type_=postgresql.JSONB(),
        existing_nullable=False,
        postgresql_using="NULL::jsonb",
    )
