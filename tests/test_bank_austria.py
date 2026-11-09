"""Bank Austria .xls importer.

All data is made up. The layout follows the BA "Umsatzliste" export: one header
row, dates as Excel serial numbers, amounts as signed numbers, and separate
name/account columns for the payer (Auftraggeber) and the payee (Empfänger).
"""
from __future__ import annotations

import datetime
import json
from decimal import Decimal

import pytest
import xlwt

from beancount_importers import bank_austria as ba


def _iban(country: str, bban: str) -> str:
    digits = "".join(str(int(ch, 36)) for ch in bban + country + "00")
    return f"{country}{98 - int(digits) % 97:02d}{bban}"


OWN = _iban("AT", "1200000000000099")
SHOP = _iban("AT", "1200000000000001")
EMPLOYER = _iban("AT", "1200000000000002")
ENERGY = _iban("AT", "1200000000000003")
LANDLORD = _iban("AT", "1200000000000004")

HEADER = [
    "Buchungsdatum", "Valutadatum", "Buchungstext", "Auftraggebername",
    "Auftraggeberkonto", "Empfängername", "Empfängerkonto", "Verwendungszweck",
    "Währung", "Betrag",
]


def _serial(iso: str) -> float:
    return float((datetime.date.fromisoformat(iso) - datetime.date(1899, 12, 30)).days)


def row(date, text, amount, payer="", payer_acct="", payee="", payee_acct="", purpose="", ccy="EUR"):
    return [_serial(date), _serial(date), text, payer, payer_acct, payee, payee_acct, purpose, ccy, amount]


ROWS = [
    # Card payment: the merchant is only in the booking text.
    row("2026-03-01", "POS 54,20 AUT K01 01.03.2026 12:34 O BILLA DANKT 4711 WIEN 1100",
        -54.2, payer="Max Muster", payer_acct=OWN),
    # Direct debit to a company, money out: the counterparty is the payee.
    row("2026-03-02", "SEPA Lastschrift", -63.5, payer="Max Muster", payer_acct=OWN,
        payee="Wien Energie GmbH", payee_acct=ENERGY, purpose="Strom Teilbetrag 03/2026"),
    # Salary, money in: the counterparty is the payer.
    row("2026-03-05", "Gutschrift", 3000.0, payer="Employer GmbH", payer_acct=EMPLOYER,
        payee="Max Muster", payee_acct=OWN, purpose="Gehalt Maerz 2026"),
    # Standing order: the name comes after "zG/".
    row("2026-03-06", "DA-NR.: 123456 zG/ Hausverwaltung Muster", -850.0, payer="Max Muster",
        payer_acct=OWN, payee_acct=LANDLORD, purpose="Miete 03/2026"),
    # Card payment in USD with an FX note at the end.
    row("2026-03-07", "E-COMM 9,99 USA K01 07.03.2026 10:00 O SPOTIFY 1234 STOCKHOLM "
        "SPESEN: 0,15 KURS:1,0832 VOM:07.03.2026", -9.37, payer="Max Muster", payer_acct=OWN),
    # Cash withdrawal.
    row("2026-03-08", "ATM 100,00 AUT K01 08.03.2026 09:00 O BANKOMAT ABC 1234 WIEN",
        -100.0, payer="Max Muster", payer_acct=OWN),
    # Second purchase at the same shop with another name, paid by transfer.
    row("2026-03-09", "Online Überweisung", -12.3, payer="Max Muster", payer_acct=OWN,
        payee="BILLA AG Filiale 12", payee_acct=SHOP, purpose="Rechnung 8812"),
    # Bank fee: both account columns hold the own IBAN.
    row("2026-03-10", "Kontoführung", -4.9, payer="Max Muster", payer_acct=OWN,
        payee="Max Muster", payee_acct=OWN, purpose="Entgelt 03/2026"),
]


def write_xls(path, rows=ROWS, header=HEADER):
    book = xlwt.Workbook()
    sheet = book.add_sheet("Umsatzliste")
    for c, value in enumerate(header):
        sheet.write(0, c, value)
    for r, values in enumerate(rows, start=1):
        for c, value in enumerate(values):
            sheet.write(r, c, value)
    book.save(str(path))
    return path


@pytest.fixture
def xls(tmp_path):
    return write_xls(tmp_path / "Umsatzliste.xls")


def by_date(result):
    return {r.date: r for r in result.rows}


# ---------------------------------------------------------------- identify ---
def test_identifies_a_bank_austria_export(xls):
    assert ba.matches(xls.read_bytes())
    assert ba.BankAustriaImporter("Assets:Giro").identify(str(xls))


def test_does_not_identify_a_csv(tmp_path):
    csv = tmp_path / "statement.csv"
    csv.write_text("Date,Description,Amount\n2026-03-01,SHOP,-1.00\n")
    assert not ba.matches(csv.read_bytes())
    assert not ba.BankAustriaImporter("Assets:Giro").identify(str(csv))


def test_does_not_identify_another_xls(tmp_path):
    other = write_xls(tmp_path / "other.xls", rows=[[1, 2]], header=["Datum", "Betrag"])
    assert not ba.matches(other.read_bytes())


# -------------------------------------------------------------- read_rows ---
def test_dates_amounts_and_currency(xls):
    rows = by_date(ba.read_rows(xls.read_bytes()))
    assert len(rows) == len(ROWS)
    assert rows["2026-03-01"].amount == Decimal("-54.20")
    assert rows["2026-03-05"].amount == Decimal("3000.00")
    assert rows["2026-03-01"].currency == "EUR"


def test_counterparty_follows_the_direction_of_the_money(xls):
    rows = by_date(ba.read_rows(xls.read_bytes()))
    assert rows["2026-03-02"].counterparty_iban == ENERGY  # money out: payee
    assert rows["2026-03-05"].counterparty_iban == EMPLOYER  # money in: payer
    assert rows["2026-03-05"].counterparty == "Employer GmbH"


def test_own_iban_is_never_the_counterparty(xls):
    rows = by_date(ba.read_rows(xls.read_bytes()))
    assert rows["2026-03-10"].counterparty_iban == ""
    assert all(r.counterparty_iban != OWN for r in rows.values())


def test_own_iban_is_detected_as_the_most_frequent(xls):
    result = ba.read_rows(xls.read_bytes())
    assert result.own_iban == OWN
    assert "most frequent" in result.own_iban_source


def test_own_iban_given_by_the_caller_wins(xls):
    spaced = " ".join(OWN[i:i + 4] for i in range(0, len(OWN), 4))
    result = ba.read_rows(xls.read_bytes(), own_iban=spaced)
    assert result.own_iban == OWN
    assert result.own_iban_source == "the account's iban metadata"


def test_unknown_own_iban_is_refused(tmp_path):
    # Every row has a different account and none of them dominates.
    rows = [row("2026-03-0%d" % i, "Gutschrift", 10.0, payer="P", payer_acct=_iban("AT", "120000000000001%d" % i))
            for i in range(1, 5)]
    path = write_xls(tmp_path / "x.xls", rows=rows)
    with pytest.raises(ba.BankAustriaError, match="own IBAN"):
        ba.read_rows(path.read_bytes())


# ----------------------------------------------------------- payee names ---
@pytest.mark.parametrize("text, expected", [
    ("POS 54,20 AUT K01 01.03.2026 12:34 O BILLA DANKT 4711 WIEN 1100", "BILLA DANKT"),
    ("ATM 100,00 AUT K01 08.03.2026 09:00 O BANKOMAT ABC 1234 WIEN", "Bargeld BANKOMAT ABC"),
    ("DA-NR.: 123456 zG/ Hausverwaltung Muster", "Hausverwaltung Muster"),
    ("E-COMM 9,99 USA K01 07.03.2026 10:00 O SPOTIFY 1234 STOCKHOLM "
     "SPESEN: 0,15 KURS:1,0832 VOM:07.03.2026", "SPOTIFY"),
    ("SEPA Lastschrift a/Wien Energie GmbH", "Wien Energie GmbH"),
    ("Wien Energie GmbH", "Wien Energie GmbH"),
])
def test_clean_payee(text, expected):
    assert ba.clean_payee(text) == expected


def test_payee_uses_the_name_column_and_falls_back_to_the_booking_text(xls):
    rows = by_date(ba.read_rows(xls.read_bytes()))
    assert rows["2026-03-02"].counterparty == "Wien Energie GmbH"
    assert rows["2026-03-01"].counterparty == "BILLA DANKT"
    assert rows["2026-03-06"].counterparty == "Hausverwaltung Muster"


def test_narration_keeps_the_original_text(xls):
    rows = by_date(ba.read_rows(xls.read_bytes()))
    assert rows["2026-03-02"].narration == "SEPA Lastschrift · Strom Teilbetrag 03/2026"
    assert "SPESEN: 0,15" in rows["2026-03-07"].narration


# ---------------------------------------------------------------- beangulp ---
def test_beangulp_extract(xls):
    importer = ba.BankAustriaImporter("Assets:Giro")
    assert importer.account(str(xls)) == "Assets:Giro"
    entries = {e.date.isoformat(): e for e in importer.extract(str(xls), [])}
    salary = entries["2026-03-05"]
    assert salary.payee == "Employer GmbH"
    assert salary.meta["counterparty-iban"] == EMPLOYER
    assert salary.postings[0].account == "Assets:Giro"
    assert salary.postings[0].units.number == Decimal("3000.00")


# ------------------------------------------------- through the MCP server ---
def test_propose_and_commit_a_bank_austria_file(book, xls):
    spaced = " ".join(OWN[i:i + 4] for i in range(0, len(OWN), 4))
    book.add_directives(f'2026-01-01 open Assets:Giro EUR\n  iban: "{spaced}"', "Open account")
    summary = json.loads(book.propose_transactions(file_path=str(xls), account="Assets:Giro"))
    assert summary["mapping"]["bank"] == "Bank Austria"
    assert summary["mapping"]["own_iban"] == OWN
    assert summary["mapping"]["own_iban_source"] == "the account's iban metadata"
    assert summary["counts"]["proposed"] == len(ROWS)
    payees = {p["payee"]: p for p in summary["payees"]}
    assert payees[ENERGY]["names"] == ["Wien Energie GmbH"]
    assert OWN not in payees

    result = json.loads(book.commit_proposal(
        summary["proposal_id"], json.dumps({"payees": {ENERGY: "Expenses:Energy"}}),
        open_new_accounts=True,
    ))
    assert result["written"] == len(ROWS)
    ledger = book.get_ledger()
    assert f'counterparty-iban: "{ENERGY}"' in ledger
    assert '"Wien Energie GmbH" "SEPA Lastschrift · Strom Teilbetrag 03/2026"' in ledger

    again = json.loads(book.propose_transactions(file_path=str(xls), account="Assets:Giro"))
    assert again["counts"]["skipped_as_already_imported"] == len(ROWS)
