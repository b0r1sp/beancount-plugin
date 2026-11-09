"""propose_transactions(file_path=...): the server reads the statement itself.

Why: with `content`, the model has to write the whole file into the tool call.
A statement of a few hundred KB does not fit in one response.
"""
from __future__ import annotations

import json

import pytest

from beancount_mcp import proposals

CSV = "\n".join([
    "Date,Description,Amount",
    "2026-03-01,SHOP,-12.50",
    "2026-03-02,EMPLOYER,3000.00",
])


@pytest.fixture
def statement(tmp_path):
    path = tmp_path / "giro-2026-03.csv"
    path.write_text(CSV)
    return path


def _propose(book, **kwargs):
    return json.loads(book.propose_transactions(account="Assets:Giro", currency="EUR", **kwargs))


def test_file_path_gives_the_same_proposal_as_content(book, statement):
    by_path = _propose(book, file_path=str(statement))
    by_content = _propose(book, content=CSV)
    assert by_path["counts"]["proposed"] == 2
    assert by_path["statement"]["statement_id"] == by_content["statement"]["statement_id"]


def test_file_name_is_recorded(book, statement):
    assert _propose(book, file_path=str(statement))["statement"]["filename"] == "giro-2026-03.csv"


def test_home_directory_is_expanded(book, isolated_home):
    (isolated_home / "giro.csv").write_text(CSV)
    assert _propose(book, file_path="~/giro.csv")["counts"]["proposed"] == 2


def test_missing_file_is_refused(book, tmp_path):
    with pytest.raises(Exception, match="No file at"):
        _propose(book, file_path=str(tmp_path / "missing.csv"))


def test_directory_is_refused(book, tmp_path):
    with pytest.raises(Exception, match="is a directory"):
        _propose(book, file_path=str(tmp_path))


def test_relative_path_is_refused(book):
    with pytest.raises(Exception, match="full path"):
        _propose(book, file_path="giro.csv")


def test_too_large_file_is_refused_before_it_is_read(book, statement, monkeypatch):
    monkeypatch.setattr(proposals, "MAX_STATEMENT_BYTES", 10)
    with pytest.raises(Exception, match="limit"):
        _propose(book, file_path=str(statement))


def test_two_sources_are_refused(book, statement):
    with pytest.raises(Exception, match="exactly one"):
        _propose(book, file_path=str(statement), content=CSV)


def test_no_source_is_refused(book):
    with pytest.raises(Exception, match="exactly one"):
        _propose(book)
