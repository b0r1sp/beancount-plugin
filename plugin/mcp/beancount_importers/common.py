"""Shapes and helpers that every bank importer shares."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

_IBAN_RE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


class ImporterError(Exception):
    """The file could not be read. Nothing was written anywhere."""


@dataclass
class Row:
    """One statement line in the common shape."""

    source_row: int          # 1-based line number in the file
    date: str                # ISO date of the booking
    amount: Decimal          # signed: negative is money out of the account
    currency: str
    counterparty: str        # cleaned name of the other side, may be ""
    counterparty_iban: str   # validated IBAN of the other side, or ""
    narration: str           # the bank's original text, whitespace collapsed


@dataclass
class Result:
    rows: list[Row]
    own_iban: str = ""
    own_iban_source: str = ""
    header_row: int = 0
    columns: dict = field(default_factory=dict)
    skipped: list[dict] = field(default_factory=list)


def normalise_iban(text) -> str:
    """The IBAN in ``text`` without spaces, uppercased — or "" if it is not a
    valid IBAN (format and ISO 13616 mod-97 checksum)."""
    candidate = re.sub(r"[\s-]+", "", str(text or "")).upper()
    if not _IBAN_RE.match(candidate):
        return ""
    rearranged = candidate[4:] + candidate[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return candidate if int(digits) % 97 == 1 else ""


def account_key(text) -> str:
    """An account number or IBAN, compared without spaces and case."""
    return re.sub(r"\s+", "", str(text or "")).upper()


def norm_header(text) -> str:
    """Lowercase ASCII words: "Empfängerkonto" -> "empfaengerkonto"."""
    low = str(text or "").lower().translate(_TRANSLIT)
    low = unicodedata.normalize("NFKD", low).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", low)


def collapse(text) -> str:
    return " ".join(str(text or "").split())
