"""The transactions template the console offers is read by the file reader."""

from pathlib import Path
from typing import get_args

from pydantic import BaseModel

from xolotl.modules.monitoring.txn.files import (
    CSV_TYPE,
    REQUIRED_COLUMNS,
    read_header,
    row_to_item,
)
from xolotl.modules.monitoring.txn.schema import TransactionInput

TEMPLATE_FILE = (
    Path(__file__).resolve().parents[4]
    / "console"
    / "public"
    / "plantilla-transacciones.csv"
)


def _model_of(annotation: object) -> type[BaseModel] | None:
    # A nested field is declared ``Model | None``; a leaf field is anything else.
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    models = [m for m in map(_model_of, get_args(annotation)) if m is not None]
    return models[0] if models else None


def _is_known(column: str) -> bool:
    model: type[BaseModel] | None = TransactionInput
    parts = column.split(".")
    for depth, part in enumerate(parts):
        if model is None or part not in model.model_fields:
            return False
        nested = _model_of(model.model_fields[part].annotation)
        if depth == len(parts) - 1:
            return nested is None
        model = nested
    return True


def test_the_console_template_header_is_read_by_the_file_reader() -> None:
    header = read_header(TEMPLATE_FILE.read_bytes(), CSV_TYPE)

    assert set(REQUIRED_COLUMNS) <= set(header)
    assert len(header) == len(set(header))
    assert [column for column in header if not _is_known(column)] == []
    # No column of the template falls into ``raw`` as an unknown extra.
    assert "raw" not in row_to_item(dict.fromkeys(header, "x"))
