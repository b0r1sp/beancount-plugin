"""tools/acceptance_bank_austria.py — the import, checked against expected numbers.

Runs on the anonymized sample. The expected numbers are computed here from
the importer's rows, so the test checks the script's mechanics: a fresh test
book, import through the plugin, comparison, and a failing report on a wrong
number.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from beancount_importers import bank_austria as ba

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "acceptance_bank_austria.py"
_spec = importlib.util.spec_from_file_location("acceptance_bank_austria", _TOOL)
acc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = acc  # dataclasses need the module to be registered
_spec.loader.exec_module(acc)

SAMPLE = Path(__file__).parent / "fixtures" / "bank_austria" / "sample.xls"
ACCOUNT = "Assets:Bank:Giro"
OPENING = Decimal("508.22")
EXTRA = [{"date": "2099-01-01", "amount": "-12.34", "narration": "Manual row from a PDF"}]


@pytest.fixture(scope="module")
def expected():
    rows = ba.read_rows(SAMPLE.read_bytes()).rows
    first = min(r.date for r in rows)
    opening_date = f"{first[:4]}-01-01"
    monthly = defaultdict(Decimal)
    monthly[opening_date[:7]] += OPENING
    for r in rows:
        monthly[r.date[:7]] += r.amount
    monthly["2099-01"] += Decimal(EXTRA[0]["amount"])
    middle = sorted(r.date for r in rows)[len(rows) // 2]
    balance_middle = OPENING + sum(r.amount for r in rows if r.date <= middle)
    total = OPENING + sum(r.amount for r in rows) + Decimal(EXTRA[0]["amount"])
    return {
        "account": ACCOUNT,
        "currency": "EUR",
        "opening": {"date": opening_date, "amount": str(OPENING)},
        "extra_transactions": EXTRA,
        "expected_entries": len(rows) + len(EXTRA),
        "control_balances": [
            {"date": middle, "balance": str(balance_middle)},
            {"date": "2099-12-31", "balance": str(total)},
        ],
        "monthly_totals": {k: str(v) for k, v in sorted(monthly.items())},
    }


@pytest.fixture
def staged(tmp_path):
    folder = tmp_path / "exports"
    folder.mkdir()
    target = folder / SAMPLE.name
    target.write_bytes(SAMPLE.read_bytes())
    return [target]


def test_all_checks_pass(expected, staged, tmp_path):
    report = acc.run(staged, expected, tmp_path / "book")
    assert report.ok, report.text()
    assert report.entries == expected["expected_entries"]
    assert report.reimport_proposed == 0


def test_a_wrong_monthly_total_fails_and_is_named(expected, staged, tmp_path):
    wrong = copy.deepcopy(expected)
    month = next(iter(wrong["monthly_totals"]))
    wrong["monthly_totals"][month] = str(Decimal(wrong["monthly_totals"][month]) + 1)
    report = acc.run(staged, wrong, tmp_path / "book")
    assert not report.ok
    assert month in report.text()
    assert "DIFF" in report.text()


def test_a_wrong_control_balance_fails(expected, staged, tmp_path):
    wrong = copy.deepcopy(expected)
    wrong["control_balances"][0]["balance"] = "0.00"
    report = acc.run(staged, wrong, tmp_path / "book")
    assert not report.ok


def test_the_book_location_setting_is_not_touched(expected, staged, tmp_path, isolated_home):
    acc.run(staged, expected, tmp_path / "book")
    assert not (isolated_home / ".beancount" / "config.json").exists()
    assert not (isolated_home / ".beancount" / "main").exists()


def test_the_test_book_is_rebuilt_on_every_run(expected, staged, tmp_path):
    acc.run(staged, expected, tmp_path / "book")
    report = acc.run(staged, expected, tmp_path / "book")
    assert report.ok, report.text()  # not "already imported"


def test_example_file_is_valid():
    example = Path(__file__).resolve().parents[1] / "tools" / "acceptance_bank_austria.example.json"
    data = acc.load_expected(example)
    assert {"account", "opening", "control_balances", "monthly_totals"} <= set(data)
