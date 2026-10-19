from alembic import context

from xolotl.models import metadata
from xolotl.platform.db import get_admin_engine

with get_admin_engine().connect() as connection:
    context.configure(
        connection=connection, target_metadata=metadata(), compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()
