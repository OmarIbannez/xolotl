"""IDF weights from the list corpus and Mexican surname frequencies."""

import pytest

from tests.modules.screening.matching.support import entry
from xolotl.modules.screening.matching import IdfTable
from xolotl.modules.screening.matching import idf as idf_module


def test_common_mexican_surnames_are_cheap_even_when_rare_on_lists():
    table = IdfTable.build([entry("1", "Juan Hernández López")])
    assert table.weight("hernandez") < 0.5
    assert table.weight("lopez") < table.weight("zamora") < 1.0
    assert table.weight("ntaganda") == pytest.approx(1.0)


def test_tokens_frequent_on_lists_are_cheap():
    entries = [entry(str(i), f"Mohammed Name{i}") for i in range(2_000)]
    table = IdfTable.build(entries)
    # 2,000 of a 10,000-entry minimum corpus: a share of 0.2.
    assert table.weight("mohammed") == pytest.approx(0.233, abs=0.001)
    assert table.weight("name7") == pytest.approx(1.0)


def test_a_small_corpus_does_not_make_every_token_common():
    table = IdfTable.build([entry("1", "Bosco Ntaganda"), entry("2", "Joseph Kony")])
    assert table.weight("bosco") == pytest.approx(1.0)


def test_the_surname_file_needs_its_population_line(monkeypatch):
    class Fake:
        def joinpath(self, _name):
            return self

        @staticmethod
        def read_text(**_kwargs):
            return "surname,count\nperez,10\n"

    def fake_files(_package: str) -> Fake:
        return Fake()

    monkeypatch.setattr("importlib.resources.files", fake_files)
    idf_module.surname_shares.cache_clear()
    try:
        with pytest.raises(TypeError, match="population"):
            _ = idf_module.surname_shares()
    finally:
        monkeypatch.undo()
        idf_module.surname_shares.cache_clear()
