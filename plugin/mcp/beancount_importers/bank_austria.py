"""Bank Austria (UniCredit) "Umsatzliste" export, old Excel format (.xls).

The export has one header row and these columns:

    Buchungsdatum, Valutadatum, Buchungstext, Auftraggebername,
    Auftraggeberkonto, Empfängername, Empfängerkonto, Verwendungszweck,
    Währung, Betrag

Dates are Excel serial numbers and ``Betrag`` is signed (negative = money out).
There is no column for the counterparty: the payer (Auftraggeber) and the
payee (Empfänger) are both given, and one of them is the account holder. So:

* money OUT went to the payee, money IN came from the payer;
* the account holder's own IBAN is never the counterparty. It is taken from
  the caller (the ``iban:`` metadata of the book account), else it is the IBAN
  that appears most often in the file — the own account is on every row;
* a card payment has no counterparty columns: the merchant is cleaned out of
  the booking text (``POS 54,20 AUT K01 … O BILLA DANKT 4711 WIEN 1100``).
"""
from __future__ import annotations

import datetime
import re
from collections import Counter
from decimal import Decimal, InvalidOperation

import beangulp
import xlrd
from beancount.core import amount as bc_amount
from beancount.core import data as bc_data

from .common import (
    ImporterError, Result, Row, account_key, collapse, norm_header, normalise_iban,
)

NAME = "Bank Austria"

#: The first 8 bytes of every old-format Excel (OLE2 compound) file.
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Normalised header -> field. Only Buchungsdatum and Betrag are required.
COLUMNS = {
    "buchungsdatum": "date",
    "valutadatum": "value_date",
    "buchungstext": "text",
    "auftraggebername": "payer_name",
    "auftraggeberkonto": "payer_account",
    "empfaengername": "payee_name",
    "empfaengerkonto": "payee_account",
    "verwendungszweck": "purpose",
    "waehrung": "currency",
    "betrag": "amount",
}
_REQUIRED = ("date", "amount")
_IDENTIFYING = ("payer_name", "payee_name", "payer_account", "payee_account")
_HEADER_SEARCH_ROWS = 10
# The own IBAN must be on at least this share of the rows that carry an IBAN.
_OWN_IBAN_QUORUM = 0.5


class BankAustriaError(ImporterError):
    pass


# ------------------------------------------------------------------ reading --
def _open(data: bytes):
    if not data.startswith(OLE_MAGIC):
        return None
    try:
        return xlrd.open_workbook(file_contents=data)
    except Exception:  # noqa: BLE001 — any unreadable workbook is "not ours"
        return None


def _find_header(sheet):
    """(row index, {field: column index}) of the header row, or None."""
    for r in range(min(_HEADER_SEARCH_ROWS, sheet.nrows)):
        found = {}
        for c, value in enumerate(sheet.row_values(r)):
            key = COLUMNS.get(norm_header(value))
            if key and key not in found:
                found[key] = c
        if all(k in found for k in _REQUIRED) and any(k in found for k in _IDENTIFYING):
            return r, found
    return None


def matches(data: bytes) -> bool:
    """Is this a Bank Austria .xls export?"""
    book = _open(data)
    if book is None or book.nsheets == 0:
        return False
    return _find_header(book.sheet_by_index(0)) is not None


def _date(value, datemode) -> str | None:
    if isinstance(value, float):
        try:
            return xlrd.xldate_as_datetime(value, datemode).date().isoformat()
        except Exception:  # noqa: BLE001
            return None
    text = collapse(value)
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _amount(value) -> Decimal | None:
    if isinstance(value, float):
        return Decimal(repr(round(value, 2))).quantize(Decimal("0.01"))
    text = collapse(value).replace(" ", "")
    if not text:
        return None
    if "," in text:  # German format: 1.234,56
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


# -------------------------------------------------------------- payee names --
_FX_RE = re.compile(r"\s*SPESEN:\s*[\d.,]+\s*KURS:\s*[\d.,]+\s*VOM:\s*[\d.]+", re.I)
_CARD_RE = re.compile(
    r"^(POS|E-COMM|ATM|AUTOMAT)\s+[\d.,]+\s+[A-Z]{2,3}\s+\w\d\d\s+[\d.]+\s+[\d:]+\s+\w\s+(.*)"
)
_STANDING_ORDER_RE = re.compile(r"^DA-NR\.:\s*\d+\s+zG/\s*(.*)", re.I)
_PREFIX_RE = re.compile(
    r"^(Echtzeitüberweisung zG/|Online Überweisung vom [\d.]+ zG/|SEPA Lastschrift a/|"
    r"Lastschrift a/|SEPA Gutschrift v/|Gutschrift a/|Gutschrift v/)\s*",
    re.I,
)
# Where the merchant name ends in a card text: a "*", a number of 3+ digits,
# a card id, a reference or a date.
_CUT_RE = re.compile(r"\s*\*|\s+\d{3,}\b|\s+[A-Z]\d\d\s|\s+REF\.|\s+\d{2}\.\d{2}/")
_CITY_RE = re.compile(r"\s+(WIEN|VIENNA|UNKNOWN)\b.*")


def clean_payee(text) -> str:
    """The counterparty's name out of a Bank Austria name or booking text."""
    original = collapse(text)
    t = _FX_RE.sub("", original)
    card = _CARD_RE.match(t)
    if card:
        t = ("Bargeld " if card.group(1) in ("ATM", "AUTOMAT") else "") + card.group(2)
    standing = _STANDING_ORDER_RE.match(t)
    if standing:
        t = standing.group(1)
    t = _PREFIX_RE.sub("", t)
    t = _CUT_RE.split(t)[0]
    t = _CITY_RE.sub("", t)
    t = re.sub(r"\s*\d+$", "", t)
    return collapse(t).strip(" ,.-/") or original


# ---------------------------------------------------------------- the rows --
def _own_iban(raw_rows, given: str) -> tuple[str, str]:
    if given:
        own = normalise_iban(given) or account_key(given)
        return own, "the account's iban metadata"
    counts: Counter = Counter()
    with_account = 0
    for values in raw_rows:
        keys = {account_key(values.get(k)) for k in ("payer_account", "payee_account")} - {""}
        if keys:
            with_account += 1
            counts.update(keys)
    if not counts:
        return "", "no account numbers in the file"
    own, seen = counts.most_common(1)[0]
    if seen < with_account * _OWN_IBAN_QUORUM:
        raise BankAustriaError(
            "Cannot tell which account in this file is your own IBAN: no account "
            "number appears on most rows. Add your IBAN to the account in the "
            'book, for example `iban: "AT12 3456 …"` under its `open` entry, '
            "and import again. Nothing was imported."
        )
    return own, f"the most frequent IBAN in the file ({seen} of {with_account} rows)"


def _counterparty(values: dict, amount: Decimal, own: str) -> tuple[str, str]:
    """(name, IBAN) of the other side of the payment."""
    payee = (values.get("payee_name"), values.get("payee_account"))
    payer = (values.get("payer_name"), values.get("payer_account"))
    primary, secondary = (payee, payer) if amount < 0 else (payer, payee)
    side = primary
    if own and account_key(primary[1]) == own:
        other = account_key(secondary[1])
        if other and other != own:
            side = secondary
    name, account = side
    if own and account_key(account) == own:
        name, account = "", ""
    cleaned = clean_payee(name) if collapse(name) else ""
    return cleaned or clean_payee(values.get("text") or values.get("purpose")), normalise_iban(account)


def read_rows(data: bytes, own_iban: str = "") -> Result:
    """Every booking in a Bank Austria .xls export, in the common row shape.

    ``own_iban`` is the account holder's IBAN, from the book. Without it, the
    most frequent IBAN in the file is taken.
    """
    book = _open(data)
    found = _find_header(book.sheet_by_index(0)) if book and book.nsheets else None
    if found is None:
        raise BankAustriaError("This is not a Bank Austria .xls export.")
    header_row, columns = found
    sheet = book.sheet_by_index(0)

    raw_rows, skipped = [], []
    for r in range(header_row + 1, sheet.nrows):
        cells = sheet.row_values(r)
        if not any(collapse(v) for v in cells):
            continue
        values = {key: cells[c] if c < len(cells) else "" for key, c in columns.items()}
        values["_line"] = r + 1
        raw_rows.append(values)

    own, own_source = _own_iban(raw_rows, own_iban)
    rows = []
    for values in raw_rows:
        line = values["_line"]
        date = _date(values.get("date"), book.datemode)
        amount = _amount(values.get("amount"))
        if date is None or amount is None:
            skipped.append({
                "row": line,
                "reason": "no date" if date is None else "no amount",
                "text": collapse(values.get("text"))[:160],
            })
            continue
        name, iban = _counterparty(values, amount, own)
        narration = " · ".join(
            part for part in (collapse(values.get("text")), collapse(values.get("purpose"))) if part
        )
        rows.append(Row(
            source_row=line,
            date=date,
            amount=amount,
            currency=collapse(values.get("currency")).upper(),
            counterparty=name,
            counterparty_iban=iban,
            narration=narration,
        ))
    return Result(
        rows=rows,
        own_iban=own,
        own_iban_source=own_source,
        header_row=header_row + 1,
        columns={field: sheet.cell_value(header_row, c) for field, c in columns.items()},
        skipped=skipped,
    )


# ---------------------------------------------------------------- beangulp --
class BankAustriaImporter(beangulp.Importer):
    """beangulp importer for the Bank Austria .xls export.

    Use it in a beangulp import script:

        import beangulp
        from beancount_importers.bank_austria import BankAustriaImporter
        beangulp.Ingest([BankAustriaImporter("Assets:Bank:BankAustria:Giro")])()

    Each transaction gets one posting on ``account``; the counter-posting is
    left out, as beangulp importers do, for categorisation later.
    """

    def __init__(self, account: str, own_iban: str = ""):
        self._account = account
        self._own_iban = own_iban

    def identify(self, filepath: str) -> bool:
        try:
            with open(filepath, "rb") as handle:
                return matches(handle.read())
        except OSError:
            return False

    def account(self, filepath: str) -> str:
        return self._account

    def extract(self, filepath: str, existing: bc_data.Entries) -> bc_data.Entries:
        with open(filepath, "rb") as handle:
            result = read_rows(handle.read(), self._own_iban)
        entries = []
        for row in result.rows:
            meta = bc_data.new_metadata(filepath, row.source_row)
            if row.counterparty_iban:
                meta["counterparty-iban"] = row.counterparty_iban
            posting = bc_data.Posting(
                self._account, bc_amount.Amount(row.amount, row.currency or "EUR"),
                None, None, None, None,
            )
            entries.append(bc_data.Transaction(
                meta, datetime.date.fromisoformat(row.date), "*",
                row.counterparty or None, row.narration,
                bc_data.EMPTY_SET, bc_data.EMPTY_SET, [posting],
            ))
        return entries
