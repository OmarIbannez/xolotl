"""Imports every ORM model module so ``Base.metadata`` is complete.

Alembic's env.py and the schema-sync test import this module. Each feature
module adds its ``models`` import here when it creates its first table.
"""

from sqlalchemy import MetaData

from xolotl.modules.tenants import models as tenants_models
from xolotl.platform.api import models as api_models
from xolotl.platform.audit import models as audit_models
from xolotl.platform.db import Base

__all__ = ["api_models", "audit_models", "metadata", "tenants_models"]


def metadata() -> MetaData:
    """``Base.metadata`` after every model module has been imported.

    Returns:
        The metadata shared by every ORM table.

    """
    return Base.metadata
