"""Smoke tests for behaviour that already exists.

They pin what the server does today, so the importer work cannot break it
unnoticed. All data here is made up.
"""
from __future__ import annotations

import json

import pytest

from beancount_mcp import importing


def _iban(country: str, bban: str) -> str:
    """A valid IBAN for a made-up account (ISO 13616 check digits)."""
    digits = "".join(str(int(ch, 36)) for ch in bban + country + "00")
    return f"{country}{98 - int(digits) % 97:02d}{bban}"


SHOP = _iban("AT", "1200000000000001")
EMPLOYER = _iban("AT", "1200000000000002")
OWN = _iban("AT", "1200000000000099")


# ------------------------------------------------------------------- IBAN ---
def test_valid_iban_is_normalised():
    spaced = " ".join(SHOP[i:i + 4] for i in range(0, len(SHOP), 4))
    assert importing.normalise_iban(spaced.lower()) == SHOP


def test_iban_with_wrong_check_digits_is_rejected():
    assert importing.normalise_iban(SHOP[:2] + "00" + SHOP[4:]) == ""


# --------------------------------------------------------------- location ---
def test_create_book_at_default_location(server, isolated_home):
    result = server.create_book("Test", "EUR")
    assert str(isolated_home / ".beancount" / "main") in result
    assert "State: exists" in server.book_location()


def test_relative_location_is_refused(server):
    with pytest.raises(Exception, match="not a full path"):
        server.set_book_location("relative/dir")


def test_create_book_refuses_to_overwrite(server, tmp_path):
    target = tmp_path / "clash"
    target.mkdir()
    (target / ".gitignore").write_text("keep me\n")
    with pytest.raises(Exception, match="overwrite"):
        server.create_book("Test", "EUR", path=str(target))
    assert (target / ".gitignore").read_text() == "keep me\n"


# ----------------------------------------------------------------- writes ---
def test_valid_transaction_is_committed(book):
    book.open_accounts("2026-01-01 open Assets:Giro EUR\n2026-01-01 open Expenses:Food EUR")
    result = book.add_transactions(
        '2026-02-01 * "Shop" "Food"\n  Expenses:Food  10.00 EUR\n  Assets:Giro'
    )
    assert result.startswith("Committed")


def test_unbalanced_transaction_is_rejected(book):
    book.open_accounts("2026-01-01 open Assets:Giro EUR\n2026-01-01 open Expenses:Food EUR")
    result = book.add_transactions(
        '2026-02-01 * "Shop" "Food"\n  Expenses:Food  10.00 EUR\n  Assets:Giro  -9.00 EUR'
    )
    assert result.startswith("REJECTED")


def test_configuration_directive_is_rejected(book):
    assert book.add_directives('option "insert_pythonpath" "TRUE"').startswith("REJECTED")


# ---------------------------------------------------------- statement CSV ---
STATEMENT = "\n".join([
    '"Auftragskonto";"Buchungstag";"Verwendungszweck";"Beguenstigter/Zahlungspflichtiger";"Kontonummer/IBAN";"Betrag";"Waehrung"',
    f'"{OWN}";"01.03.26";"SHOP SAGT DANKE 4711";"Shop GmbH";"{SHOP}";"-54,20";"EUR"',
    f'"{OWN}";"03.03.26";"Einkauf 8812";"Shop GmbH Filiale 12";"{SHOP}";"-12,30";"EUR"',
    f'"{OWN}";"05.03.26";"Gehalt Maerz";"Employer GmbH";"{EMPLOYER}";"3.000,00";"EUR"',
    f'"{OWN}";"08.03.26";"Baeckerei";"";"";"-3,50";"EUR"',
])


def _propose(book):
    return json.loads(book.propose_transactions(content=STATEMENT, account="Assets:Giro"))


def test_statement_payees_are_grouped_by_counterparty_iban(book):
    summary = _propose(book)
    assert summary["mapping"]["columns"]["counterparty_iban"] == "Kontonummer/IBAN"
    payees = {p["payee"]: p for p in summary["payees"]}
    assert payees[SHOP]["rows"] == 2  # two spellings, one IBAN
    assert OWN not in payees  # the own-account column is ignored
    assert "BAECKEREI" in payees  # no IBAN: grouped by name


def test_commit_writes_payee_and_iban_and_reimport_finds_duplicates(book):
    summary = _propose(book)
    spaced_shop = " ".join(SHOP[i:i + 4] for i in range(0, len(SHOP), 4))
    overrides = {"payees": {spaced_shop: "Expenses:Food", EMPLOYER: "Income:Salary"}}
    result = json.loads(book.commit_proposal(
        summary["proposal_id"], json.dumps(overrides), open_new_accounts=True,
    ))
    assert result["written"] == 4
    ledger = book.get_ledger()
    assert f'counterparty-iban: "{SHOP}"' in ledger
    assert '"Shop GmbH" "SHOP SAGT DANKE 4711"' in ledger

    again = _propose(book)
    assert again["counts"]["skipped_as_already_imported"] == 4
    assert again["counts"]["proposed"] == 0
