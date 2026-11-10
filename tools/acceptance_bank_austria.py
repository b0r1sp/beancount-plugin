"""Acceptance test: import real Bank Austria exports and compare with your book.

Run it on YOUR computer. It never touches your book: it builds a separate test
book (default: tmp/acceptance-book/, git-ignored), imports every export into
it through the plugin (propose_transactions with file_path, then
commit_proposal), and compares the result with the numbers you expect:

    uv run --project plugin --group test python tools/acceptance_bank_austria.py \\
        --expected tmp/acceptance.json ~/path/to/data/Girokonto/*.xls

The expected numbers are your financial data, so they live in a local file
(tmp/ is git-ignored). tools/acceptance_bank_austria.example.json shows the
format:

* account, currency, own_iban (optional: else from the file names)
* opening           — date and amount of the opening balance
* extra_transactions — bookings that are in no export (e.g. rows from a PDF)
* expected_entries  — imported rows plus extra transactions
* control_balances  — the account balance at the end of a date
* monthly_totals    — the account's net change per month (opening included)

Amounts may be written 1974.83 or 1.974,83. The exit code is 0 when every
check passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin" / "mcp"))
from beancount_mcp import server  # noqa: E402

MARKER = "acceptance-book"  # a file inside .git/ marking a book this script made
OPENING_NARRATION = "Opening balance (acceptance test)"
MANUAL_ACCOUNT = "Equity:Acceptance-Manual"
TOLERANCE = Decimal("0.005")


def money(value) -> Decimal:
    text = str(value).strip().replace(" ", "")
    if "," in text:  # German format: 1.974,83
        text = text.replace(".", "").replace(",", ".")
    return Decimal(text).quantize(Decimal("0.01"))


def load_expected(path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = {"account", "opening", "control_balances", "monthly_totals"} - set(data)
    if missing:
        raise SystemExit(f"{path}: missing {sorted(missing)}")
    return data


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    ok: bool = True
    entries: int = 0
    reimport_proposed: int = 0

    def check(self, passed: bool, line: str) -> None:
        self.ok = self.ok and passed
        self.lines.append(("  OK    " if passed else "  DIFF  ") + line)

    def info(self, line: str) -> None:
        self.lines.append("        " + line)

    def text(self) -> str:
        verdict = "PASSED" if self.ok else "FAILED"
        return "\n".join(self.lines + ["", f"Acceptance test {verdict}."])


@contextlib.contextmanager
def _book_at(path: Path):
    """Point the plugin at the test book for the duration of the run."""
    before = os.environ.get(server.BOOK_ENV)
    os.environ[server.BOOK_ENV] = str(path)
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(server.BOOK_ENV, None)
        else:
            os.environ[server.BOOK_ENV] = before


def _fresh(path: Path) -> None:
    """Remove a previous test book — only one this script made."""
    if path.exists():
        if not (path / ".git" / MARKER).exists():
            raise SystemExit(
                f"{path} exists and was not made by this script. Choose another "
                f"--book-dir; nothing was deleted."
            )
        shutil.rmtree(path)


def _setup(expected: dict) -> str:
    account = expected["account"]
    ccy = expected.get("currency", "EUR")
    opening = expected["opening"]
    # Accounts open from 1970, so a row dated before the opening balance is
    # still accepted (and then shows up as a difference, not a crash).
    lines = [f"1970-01-01 open {account} {ccy}"]
    if expected.get("own_iban"):
        lines.append(f'  iban: "{expected["own_iban"]}"')
    lines.append(f"1970-01-01 open {MANUAL_ACCOUNT}")
    lines.append("")
    lines.append(f'{opening["date"]} * "{OPENING_NARRATION}"')
    lines.append(f"  {account}  {money(opening['amount'])} {ccy}")
    lines.append("  Equity:Opening-Balances")
    for extra in expected.get("extra_transactions", []):
        narration = str(extra.get("narration", "Manual")).replace('"', "'")
        lines += ["", f'{extra["date"]} * "{narration}"',
                  f"  {account}  {money(extra['amount'])} {ccy}", f"  {MANUAL_ACCOUNT}"]
    return "\n".join(lines)


def _postings(book: Path, account: str):
    from beancount import loader
    from beancount.core import data as bc

    entries, errors, _ = loader.load_file(str(book / "main.beancount"))
    if errors:
        raise SystemExit(f"The test book does not load: {errors[:3]}")
    rows = []
    for entry in entries:
        if isinstance(entry, bc.Transaction):
            for posting in entry.postings:
                if posting.account == account:
                    rows.append((entry.date.isoformat(), posting.units.number, entry.narration))
    return rows


def run(files, expected: dict, book_dir: Path) -> Report:
    book_dir = Path(book_dir).resolve()
    account = expected["account"]
    report = Report()
    _fresh(book_dir)
    with _book_at(book_dir):
        server.create_book("Acceptance test", expected.get("currency", "EUR"))
        (book_dir / ".git" / MARKER).write_text("made by tools/acceptance_bank_austria.py\n")
        result = server.add_directives(_setup(expected), "Acceptance test: accounts and opening")
        if result.startswith("REJECTED"):
            raise SystemExit(result)

        report.lines.append(f"Test book: {book_dir}")
        report.lines.append("Import:")
        for path in files:
            path = Path(path).resolve()
            summary = json.loads(server.propose_transactions(file_path=str(path), account=account))
            proposed = summary["counts"]["proposed"]
            report.info(f"{path.name}: {proposed} proposed, own IBAN from "
                        f"{summary['mapping'].get('own_iban_source', '?')}")
            if proposed:
                try:
                    server.commit_proposal(summary["proposal_id"], open_new_accounts=True)
                except Exception as exc:  # noqa: BLE001 — reported, not raised
                    report.check(False, f"{path.name}: the import was refused: {str(exc)[:300]}")

        report.reimport_proposed = sum(
            json.loads(server.propose_transactions(file_path=str(Path(p).resolve()), account=account))
            ["counts"]["proposed"]
            for p in files
        )

    rows = _postings(book_dir, account)
    imported = [r for r in rows if r[2] != OPENING_NARRATION]
    report.entries = len(imported)
    ledger = (book_dir / "transactions.beancount").read_text(encoding="utf-8")

    report.lines.append("Checks:")
    want = int(expected.get("expected_entries", report.entries))
    report.check(report.entries == want, f"entries: {report.entries} (expected {want})")
    report.check(report.reimport_proposed == 0,
                 f"re-import of all files proposes {report.reimport_proposed} new rows (expected 0)")
    report.info(f"rows with a counterparty IBAN: {ledger.count('counterparty-iban:')}")

    for control in expected.get("control_balances", []):
        balance = sum((amount for date, amount, _ in rows if date <= control["date"]), Decimal(0))
        want_balance = money(control["balance"])
        report.check(abs(balance - want_balance) <= TOLERANCE,
                     f"balance at the end of {control['date']}: {balance:.2f} (expected {want_balance:.2f})")

    actual = defaultdict(Decimal)
    for date, amount, _ in rows:
        actual[date[:7]] += amount
    wanted = {month: money(v) for month, v in expected.get("monthly_totals", {}).items()}
    for month in sorted(set(actual) | set(wanted)):
        got, want_month = actual.get(month, Decimal(0)), wanted.get(month)
        if want_month is None:
            report.info(f"{month}: {got:.2f} (no expected value)")
            continue
        report.check(abs(got - want_month) <= TOLERANCE,
                     f"{month}: {got:.2f} (expected {want_month:.2f})")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path, help="Bank Austria .xls exports")
    parser.add_argument("--expected", type=Path, required=True, help="your expected numbers (JSON, keep it in tmp/)")
    parser.add_argument("--book-dir", type=Path, default=Path("tmp/acceptance-book"),
                        help="where the test book is built (git-ignored; rebuilt on every run)")
    args = parser.parse_args(argv)
    report = run(args.files, load_expected(args.expected), args.book_dir)
    print(report.text())
    sys.exit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
