"""Imports every ORM model module so ``Base.metadata`` is complete.

Alembic's env.py and the schema-sync test import this module. Each feature
module adds its ``models`` import here when it creates its first table.
"""

from sqlalchemy import MetaData

from xolotl.modules.tenants import models as tenants_models
from xolotl.platform.api import models as api_models
from xolotl.platform.audit import models as audit_models
from xolotl.platform.calendar import models as calendar_models
from xolotl.platform.crypto import models as crypto_models
from xolotl.platform.db import Base
from xolotl.platform.events import models as events_models
from xolotl.platform.jobs import models as jobs_models
from xolotl.platform.policy import models as policy_models
from xolotl.platform.units import models as units_models
from xolotl.platform.workflows import models as workflows_models

__all__ = [
    "api_models",
    "audit_models",
    "calendar_models",
    "crypto_models",
    "events_models",
    "jobs_models",
    "metadata",
    "policy_models",
    "tenants_models",
    "units_models",
    "workflows_models",
]


def metadata() -> MetaData:
    """``Base.metadata`` after every model module has been imported.

    Returns:
        The metadata shared by every ORM table.

    """
    return Base.metadata
