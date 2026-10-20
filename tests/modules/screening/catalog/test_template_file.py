"""The template the console offers for download follows the template reader."""

from pathlib import Path

from xolotl.modules.screening.catalog.template import TEMPLATE_COLUMNS

TEMPLATE_FILE = (
    Path(__file__).resolve().parents[4]
    / "console"
    / "public"
    / "plantilla-lista-privada.csv"
)


def test_the_console_template_header_is_the_template_columns() -> None:
    first_line = TEMPLATE_FILE.read_text(encoding="utf-8").splitlines()[0]

    assert tuple(first_line.split(",")) == TEMPLATE_COLUMNS
