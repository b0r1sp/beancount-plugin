"""The Bank Austria importer on anonymized real data.

tests/fixtures/bank_austria/ holds 200 rows of real exports, anonymized with
tools/anonymize_bank_austria.py: people, IBANs and long numbers are replaced,
amounts are scaled by one factor and dates shifted by one offset. The layout,
the texts and the edge cases are real.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from beancount_importers import bank_austria as ba
from beancount_importers.common import normalise_iban

FIXTURE = Path(__file__).parent / "fixtures" / "bank_austria"
XLS = FIXTURE / "sample.xls"
JSONL = FIXTURE / "rows.jsonl"


@pytest.fixture(scope="module")
def result():
    return ba.read_rows(XLS.read_bytes())


@pytest.fixture(scope="module")
def jsonl_rows():
    lines = JSONL.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[1])["header"]
    return header, [json.loads(line)["cells"] for line in lines[2:]]


def test_every_row_is_read(result, jsonl_rows):
    _, rows = jsonl_rows
    assert ba.matches(XLS.read_bytes())
    assert len(result.rows) == len(rows) == 200
    assert result.skipped == []


def test_xls_and_jsonl_hold_the_same_amounts(result, jsonl_rows):
    header, rows = jsonl_rows
    betrag = header.index("Betrag")
    from_jsonl = sorted(Decimal(repr(r[betrag])).quantize(Decimal("0.01")) for r in rows)
    assert sorted(r.amount for r in result.rows) == from_jsonl


def test_own_iban_is_found_and_never_a_counterparty(result):
    assert result.own_iban
    assert "both account columns" in result.own_iban_source
    assert all(r.counterparty_iban != result.own_iban for r in result.rows)


def test_counterparty_ibans_are_valid(result):
    ibans = [r.counterparty_iban for r in result.rows if r.counterparty_iban]
    assert ibans
    assert all(normalise_iban(i) == i for i in ibans)


def test_every_row_has_a_payee_without_card_noise(result):
    for r in result.rows:
        assert r.counterparty, r.narration
        assert not r.counterparty.startswith(("POS ", "E-COMM ", "ATM ", "AUTOMAT ")), r.counterparty
        assert "SPESEN:" not in r.counterparty
        assert "DA-NR" not in r.counterparty


def test_payees_have_no_leftover_text(result):
    # Patterns that came up in the real exports and are cleaned away.
    import re
    for r in result.rows:
        p = r.counterparty
        assert not p.startswith("'") and not p[:1].isdigit(), p
        assert not re.search(r"[A-Z]{2}\d{2}[A-Z0-9]{8,}", p), p  # an IBAN
        assert "Ref:" not in p, p


def test_pseudonyms_stay_distinct(result):
    people = {r.counterparty for r in result.rows if r.counterparty.startswith("Person")}
    assert "Person" not in people  # a pseudonym that lost its letters


def test_direct_debits_get_the_creditor_iban(result):
    debits = [r for r in result.rows if r.narration.upper().startswith("SEPA LASTSCHRIFT") and r.amount < 0]
    assert debits
    with_iban = [r for r in debits if r.counterparty_iban]
    assert with_iban  # the creditor's IBAN, not lost to the direction rule
    assert all(r.counterparty_iban != result.own_iban for r in debits)


def test_dates_and_currency(result):
    assert all(len(r.date) == 10 and r.date[4] == "-" for r in result.rows)
    assert {r.currency for r in result.rows} <= {"EUR"}


def test_import_commit_and_reimport(book, tmp_path):
    path = tmp_path / XLS.name
    path.write_bytes(XLS.read_bytes())
    book.add_directives("2020-01-01 open Assets:Giro EUR", "Open account")
    summary = json.loads(book.propose_transactions(file_path=str(path), account="Assets:Giro"))
    assert summary["mapping"]["bank"] == "Bank Austria"
    assert summary["counts"]["proposed"] == 200

    result = json.loads(book.commit_proposal(summary["proposal_id"], open_new_accounts=True))
    assert result["written"] == 200  # bean-check accepted every entry

    again = json.loads(book.propose_transactions(file_path=str(path), account="Assets:Giro"))
    assert again["counts"]["skipped_as_already_imported"] == 200
    assert again["counts"]["proposed"] == 0
