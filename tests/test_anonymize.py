"""tools/anonymize_bank_austria.py — test data from real exports, without personal data."""
from __future__ import annotations

import importlib.util
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
import xlrd

from beancount_importers import bank_austria as ba
from beancount_importers.common import normalise_iban
from test_bank_austria import COL, EMPLOYER, ENERGY, LANDLORD, OWN, ROWS, row, write_xls

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "anonymize_bank_austria.py"
_spec = importlib.util.spec_from_file_location("anonymize_bank_austria", _TOOL)
anon = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(anon)

PERSON_ROWS = ROWS + [
    # A private person, money out, name also in the text.
    row("2026-03-11", "Online Überweisung zG/ Anna Beispiel", -200.0, payer="Max Muster",
        payer_acct=OWN, payee="Anna Beispiel", payee_acct="AT611904300234573201",
        purpose="Geburtstag Anna Beispiel Vertrag 9876543"),
    # A private person written in capitals in the name column (the real
    # export does this): must be replaced, not kept as a "merchant".
    row("2026-03-13", "Online Überweisung", -30.0, payer="Max Muster", payer_acct=OWN,
        payee="KARL BEISPIELMANN", payee_acct="AT483200000012345864", purpose="Danke"),
    # The energy supplier again: both rows must keep sharing one fake IBAN.
    row("2026-03-12", "SEPA Lastschrift", -41.0, payer="Max Muster", payer_acct=OWN,
        payee="Wien Energie GmbH", payee_acct=ENERGY, purpose="Gas Teilbetrag 03/2026"),
]


@pytest.fixture
def source(tmp_path):
    return write_xls(tmp_path / "Umsatzliste_2026_03.xls", rows=PERSON_ROWS)


@pytest.fixture
def result(source, tmp_path):
    out = tmp_path / "out"
    anon.run([source], out, seed=7, target=50)
    return out


def _texts(out: Path) -> str:
    return (out / "rows.jsonl").read_text(encoding="utf-8")


def test_outputs_are_written(result):
    assert {p.name for p in result.iterdir()} >= {"rows.jsonl", "sample.xls", "preview.csv", "review.txt"}


def test_no_real_iban_survives(result):
    text = _texts(result) + (result / "preview.csv").read_text(encoding="utf-8")
    for real in (OWN, EMPLOYER, ENERGY, LANDLORD, "AT611904300234573201"):
        assert real not in text


def test_fake_ibans_are_valid_and_consistent(result):
    data = ba.read_rows((result / "sample.xls").read_bytes())
    ibans = Counter(r.counterparty_iban for r in data.rows if r.counterparty_iban)
    assert all(normalise_iban(i) == i for i in ibans)
    assert data.own_iban and data.own_iban != OWN
    assert ibans.most_common(1)[0][1] == 2  # the supplier's two rows still share one IBAN


def test_people_are_replaced_and_companies_kept(result):
    text = _texts(result)
    assert "Anna Beispiel" not in text
    assert "BEISPIELMANN" not in text.upper()
    assert "Max Muster" not in text or "Max Muster" in anon.HOLDER_PSEUDONYM
    assert "Wien Energie GmbH" in text
    assert "BILLA DANKT" in text
    review = (result / "review.txt").read_text(encoding="utf-8")
    assert "Anna Beispiel" in review  # listed for the user's check, never committed


def test_long_numbers_are_replaced(result):
    text = _texts(result)
    assert "9876543" not in text
    assert "123456" not in text  # standing-order number


def test_amounts_are_scaled_and_dates_shifted(source, result):
    original = ba.read_rows(source.read_bytes())
    fake = ba.read_rows((result / "sample.xls").read_bytes())
    meta = json.loads((result / "rows.jsonl").read_text(encoding="utf-8").splitlines()[0])
    factor = Decimal(meta["amount_factor"])
    assert factor != 1 and meta["date_offset_days"] != 0
    total_original = sum(r.amount for r in original.rows)
    total_fake = sum(r.amount for r in fake.rows)
    assert abs(total_fake - (total_original * factor)) <= Decimal("0.01") * len(fake.rows)
    assert {r.date for r in fake.rows}.isdisjoint({r.date for r in original.rows})


def test_jsonl_keeps_cell_types(result):
    lines = (result / "rows.jsonl").read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[1])["header"]
    first = json.loads(lines[2])["cells"]
    assert "Buchungsdatum" in header
    assert isinstance(first[header.index("Buchungsdatum")], float)  # Excel date stays a number
    assert isinstance(first[header.index("Betrag")], float)


def test_selection_covers_every_kind_and_respects_the_target(tmp_path):
    many = PERSON_ROWS * 40
    path = write_xls(tmp_path / "big.xls", rows=many)
    out = tmp_path / "out"
    anon.run([path], out, seed=1, target=30)
    sample = xlrd.open_workbook(str(out / "sample.xls")).sheet_by_index(0)
    assert sample.nrows - 1 <= 30
    text, amount = COL["Buchungstext"], COL["Betrag"]
    kinds = {anon.kind(sample.row_values(r)[text], sample.row_values(r)[amount])
             for r in range(1, sample.nrows)}
    original_kinds = {anon.kind(r[text], r[amount]) for r in PERSON_ROWS}
    assert kinds == original_kinds


def test_a_name_replaced_once_is_replaced_in_any_case(tmp_path):
    rows = [
        row("2026-03-01", "Echtzeitüberweisung zG/ Karl Beispielmann", -10.0),
        row("2026-03-02", "Online Überweisung", -20.0, payer_acct=OWN,
            payee="KARL BEISPIELMANN", payee_acct="AT483200000012345864"),
    ]
    out = tmp_path / "out"
    anon.run([write_xls(tmp_path / "x.xls", rows=rows)], out, seed=2, target=10)
    assert "BEISPIELMANN" not in (out / "rows.jsonl").read_text(encoding="utf-8").upper()


@pytest.mark.parametrize("name, company", [
    ("Wien Energie GmbH", True),
    ("EMPLOYER GMBH", True),
    ("Muster Handels Gm", True),  # cut off in a booking text
    ("KARL BEISPIELMANN", False),  # capitals alone do not make a company
    ("Anna Beispiel", False),
])
def test_is_company(name, company):
    assert anon.is_company(name, set()) is company


def test_files_agree_on_the_own_iban_from_their_names(tmp_path):
    # Like the real exports: the IBAN is in every file name, and one file has
    # no own account on any row (only card payments and debits).
    first = write_xls(tmp_path / f"1-Umsatzliste-a-{OWN}.xls")
    second = write_xls(tmp_path / f"1-Umsatzliste-b-{OWN}.xls", rows=[
        row("2026-04-01", "HOFER DANKT 2371 WIEN", -23.4),
        row("2026-04-02", "SEPA Lastschrift", -63.5, payee="Wien Energie GmbH", payee_acct=ENERGY),
        row("2026-04-03", "SEPA Lastschrift", -41.0, payee="Wien Energie GmbH", payee_acct=ENERGY),
    ])
    report = anon.run([first, second], tmp_path / "out", seed=4, target=50)
    assert not any("WARNING" in line for line in report)


def test_pseudonyms_contain_no_digits(result):
    # The payee clean-up removes numbers at the end of a name, so "Person 01"
    # and "Person 02" would both become "Person".
    import re
    pseudonyms = set(re.findall(r"Person \S+", _texts(result)))
    assert pseudonyms
    assert not any(ch.isdigit() for p in pseudonyms for ch in p)


def test_card_payments_are_one_kind():
    # Newer card texts start with the merchant: one kind, not one per merchant.
    assert anon.kind("HOFER DANKT 2371 WIEN", -23.4) == anon.kind("MCDONALDS155 2360", -11.8)
    assert anon.kind("HOFER DANKT 2371 WIEN", -23.4) != anon.kind("SEPA Lastschrift", -63.5)
    assert anon.kind("Echtzeitüberweisung zG/ Anna", -5.0) != anon.kind("HOFER DANKT", -5.0)
    assert anon.kind("'25,00 % KESt", -1.0) == "KEST / out"


def test_review_lists_merchants_from_card_texts(result):
    # A merchant can be a person's name; the review must show it.
    review = (result / "review.txt").read_text(encoding="utf-8")
    assert "Merchants in card texts" in review
    assert "BILLA DANKT" in review.split("Merchants in card texts")[1]


def test_replace_list_also_replaces_merchants_in_card_texts(source, tmp_path):
    out = tmp_path / "out"
    anon.run([source], out, seed=7, target=50, names={"replace": ["BILLA DANKT"]})
    assert "BILLA DANKT" not in _texts(out)


def test_rename_replaces_a_company_with_a_neutral_name(source, tmp_path):
    # An employer is a company, so it would be kept; "rename" swaps it for a
    # neutral company name (not a person pseudonym), in name columns and texts.
    out = tmp_path / "out"
    anon.run([source], out, seed=7, target=50,
             names={"rename": {"Wien Energie GmbH": "SUPPLIER GMBH", "billa dankt": "SHOP DANKT"}})
    text = _texts(out)
    assert "Wien Energie" not in text
    assert "SUPPLIER GMBH" in text
    assert "BILLA DANKT" not in text and "SHOP DANKT" in text
    review = (out / "review.txt").read_text(encoding="utf-8")
    assert "Wien Energie GmbH  ->  SUPPLIER GMBH" in review


def test_same_seed_gives_the_same_output(source, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    anon.run([source], a, seed=3, target=50)
    anon.run([source], b, seed=3, target=50)
    assert (a / "rows.jsonl").read_text() == (b / "rows.jsonl").read_text()
