"""Bank-specific statement importers.

Each module reads one bank's export format and returns rows in one common
shape (date, signed amount, currency, counterparty name and IBAN, narration).
The beancount-mcp server feeds those rows into its proposal pipeline, which
does the rest: duplicate detection, grouping by counterparty, proposal, commit.

Each module also offers a ``beangulp.Importer`` class, so the importer works
without the plugin too, from a beangulp import script.

This package does not import ``beancount_mcp``: it can move to its own
repository without changes.
"""
from . import bank_austria

#: Every bank importer, in the order they are tried. Each module has
#: ``NAME``, ``matches(data) -> bool`` and ``read_rows(data, own_iban, filename) -> Result``.
BANKS = (bank_austria,)


def detect(data: bytes):
    """The bank importer module for these bytes, or None."""
    for module in BANKS:
        if module.matches(data):
            return module
    return None
