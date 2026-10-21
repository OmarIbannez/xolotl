"""Code that runs in a fresh interpreter started by ``multiprocessing``'s spawn.

It imports no ``xolotl`` module at the top, so the caller's imports cannot leak
into the child.
"""

import importlib


def tables_registered_after_importing(module: str) -> list[str]:
    """Import ``module`` and list the partitioned tables registered by then.

    Returns:
        The registered table names, in registration order.

    """
    _ = importlib.import_module(module)
    partitions = importlib.import_module("xolotl.platform.partitions")
    return [spec.table for spec in partitions.partitioned_tables()]
