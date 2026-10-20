"""Malformed data tables fail loudly on first use."""

import pytest

from xolotl.modules.screening.matching import tables


@pytest.fixture
def fake_files(monkeypatch):
    contents: dict[str, object] = {}
    monkeypatch.setattr(tables, "_load", contents.__getitem__)
    for loader in (tables.word_tables, tables.legal_forms, tables.nickname_groups):
        loader.cache_clear()
    yield contents
    for loader in (tables.word_tables, tables.legal_forms, tables.nickname_groups):
        loader.cache_clear()


@pytest.mark.parametrize(
    ("name", "content", "loader", "message"),
    [
        ("particles.yaml", ["de"], "word_tables", "must be a mapping"),
        ("particles.yaml", {"particles": "de"}, "word_tables", "list of words"),
        (
            "particles.yaml",
            {"particles": ["de", 3], "honorifics": [], "generic": []},
            "word_tables",
            "not a word",
        ),
        ("legal_forms.yaml", {"SA": [""]}, "legal_forms", "not a word"),
        (
            "nicknames_es.yaml",
            {"groups": {"a": 1}},
            "nickname_groups",
            "must be a list",
        ),
    ],
)
def test_malformed_tables_raise(fake_files, name, content, loader, message):
    fake_files[name] = content
    with pytest.raises(TypeError, match=message):
        _ = getattr(tables, loader)()
