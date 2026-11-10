"""Make test data from real Bank Austria exports — without personal data.

Run it on YOUR computer, on your own exports. It writes to a folder that git
ignores (default: tmp/anonymized/), so nothing reaches the repository until
you have reviewed it and copied it yourself:

    uv run --project plugin --group test python tools/anonymize_bank_austria.py \\
        ~/path/to/data/Girokonto/*.xls

What it does:

* reads every file with the plugin's own Bank Austria importer first and
  prints how many rows it read and which it skipped (a first real-data test);
* replaces every IBAN and account number, the account holder's name, the
  names of private persons, and every number of 5+ digits (standing-order,
  contract and reference numbers) — consistently: the same real value always
  becomes the same fake value, so grouping by IBAN can still be tested;
* keeps company names (GmbH, AG, Bank, …) and the names in card texts
  (BILLA DANKT …) — the rules need them; any other name is replaced;
* multiplies all amounts by one random factor and shifts all dates by one
  random number of days;
* picks a sample (default 200 rows) that covers every kind of booking.

Output (in the output folder):

* rows.jsonl   — line 1: factor and offset; line 2: header; then one row per
                 line with the cell values and their types (numbers stay numbers)
* sample.xls   — the same rows as a real .xls file
* preview.csv  — the same rows as a table, for your review (do not commit)
* review.txt   — which names were kept and which were replaced (do not commit)

Review preview.csv and review.txt. To change a decision, list names in a JSON
file and pass it with --names:

    {"keep": ["..."], "replace": ["..."], "rename": {"Real Company AG": "EMPLOYER GMBH"}}

"keep" keeps a name, "replace" turns it into a person pseudonym, and "rename"
swaps a company for a neutral company name of your choice (e.g. an employer),
in the name columns and in all texts. Keep this file in tmp/: it holds real names.
Then copy rows.jsonl and sample.xls to tests/fixtures/bank_austria/.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import xlrd
import xlwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin" / "mcp"))
from beancount_importers import bank_austria as ba  # noqa: E402
from beancount_importers.common import account_key, collapse, norm_header, normalise_iban  # noqa: E402

HOLDER_PSEUDONYM = "Konto Inhaber"
DEFAULT_TARGET = 200
PER_KIND = 8

# Words that mark a name as an organisation, not a private person.
_COMPANY_WORDS = {
    # Legal forms, also cut off as in booking texts ("… Gm", "… Gmb").
    "gmbh", "gm", "gmb", "ag", "og", "kg", "eu", "gesmbh", "gesellschaft", "se", "sa",
    "sarl", "sca", "srl", "spa", "ab", "oy", "as", "ltd",
    "inc", "llc", "bv", "nv", "plc", "co", "bank", "sparkasse", "raiffeisen",
    "versicherung", "versicherungen", "magistrat", "stadt", "gemeinde", "finanzamt",
    "republik", "verein", "verband", "gesundheitskasse", "sozialversicherung", "svs",
    "oegk", "energie", "vertrieb", "service", "services", "shop", "store", "markt",
    "hausverwaltung", "immobilien", "apotheke", "hotel", "restaurant", "cafe",
}
_IBAN_IN_TEXT = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b")
_LONG_NUMBER = re.compile(r"\d{5,}")
_DATE_IN_TEXT = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
_CARD_AMOUNT = re.compile(r"^((?:POS|E-COMM|ATM|AUTOMAT)\s+)([\d.,]+)")
_NAME_IN_TEXT = re.compile(r"(?:zG/|a/|v/)\s*([^\d|/]+?)(?=\s+\d|\s*$|\s+REF)")


# ----------------------------------------------------------------- reading --
def load(path: Path):
    """(header, rows, datemode) of one export, rows as raw cell values."""
    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(0)
    found = ba._find_header(sheet)
    if found is None:
        raise SystemExit(f"{path}: not a Bank Austria export (no header row found).")
    header_row, _ = found
    header = [collapse(v) for v in sheet.row_values(header_row)]
    rows = [sheet.row_values(r) for r in range(header_row + 1, sheet.nrows)
            if any(collapse(v) for v in sheet.row_values(r))]
    return header, rows, book.datemode


# First words of bank booking texts. A text that starts with anything else
# is a card payment in the newer format, where the merchant comes first.
_BOOKING_WORDS = {
    "SEPA", "LASTSCHRIFT", "GUTSCHRIFT", "ÜBERWEISUNG", "ONLINE", "ECHTZEITÜBERWEISUNG",
    "ECHTZEITGUTSCHRIFT", "DAUERAUFTRAG", "EINZAHLUNG", "ABHEBUNG", "BANKOMAT", "ENTGELT",
    "KONTOFÜHRUNG", "KONTOPAKET", "KARTENPREIS", "PRÄMIE", "SOLLZINSEN", "HABENZINSEN",
    "ZINSEN", "KEST", "STORNO", "INFORMATION", "RÜCKBUCHUNG",
}


def kind(text, amount) -> str:
    """What sort of booking a row is, for an even sample."""
    t = collapse(text).upper()
    first = re.sub(r"[^A-ZÄÖÜ-]", "", (t.lstrip("'0123456789.,% ").split() or [""])[0])
    for prefix in ("POS", "E-COMM", "ATM", "AUTOMAT", "DA-NR"):
        if t.startswith(prefix):
            name = prefix
            break
    else:
        name = None
    if name is None and t and first not in _BOOKING_WORDS:
        name = "CARD (merchant first)"
    if name is None:
        # The first two words, without names after "zG/", "a/", "v/" and
        # without numbers: "GUTSCHRIFT A/ANNA" and "GUTSCHRIFT A/OTTO" are one kind.
        words = []
        for word in t.lstrip("'0123456789.,% ").split()[:2]:
            if "/" in word:
                words.append(word.split("/")[0] + "/")
                break
            if not any(ch.isdigit() for ch in word):
                words.append(word)
        name = " ".join(words) or "(empty)"
    if "SPESEN:" in t and "KURS:" in t:
        name += " +FX"
    if "STORNO" in t:
        name += " +STORNO"
    try:
        direction = "in" if float(amount) > 0 else "out"
    except (TypeError, ValueError):
        direction = "?"
    return f"{name} / {direction}"


def is_company(name: str, keep: set) -> bool:
    """Is this name an organisation? Only a company word or the keep list
    makes it one. Capitals do not: the export writes some PEOPLE in capitals
    too, and when in doubt a name is replaced, not kept."""
    if collapse(name).lower() in keep:
        return True
    words = set(re.findall(r"[a-zäöüß]+", collapse(name).lower().replace(".", "")))
    return bool(words & _COMPANY_WORDS)


# ------------------------------------------------------------- anonymising --
class Anonymiser:
    def __init__(self, seed: int, keep=(), replace=(), rename=None):
        self.rng = random.Random(seed)
        self.salt = str(self.rng.random()).encode()
        self.factor = Decimal(str(round(self.rng.uniform(0.6, 1.4), 4)))
        if self.factor == 1:
            self.factor = Decimal("1.1")
        self.offset = self.rng.choice([-1, 1]) * self.rng.randint(30, 300)
        self.keep = {n.lower() for n in keep}
        self.force_replace = {n.lower() for n in replace}
        # Company -> neutral company name, chosen by the user.
        self.rename = {collapse(k).lower(): v for k, v in (rename or {}).items()}
        self.renamed: dict[str, str] = {collapse(k): v for k, v in (rename or {}).items()}
        self.ibans: dict[str, str] = {}
        self.numbers: dict[str, str] = {}
        self.people: dict[str, str] = {}
        self.spelling: dict[str, str] = {}  # lower-case key -> name as written
        self.kept: set[str] = set()
        self.holder_names: set[str] = set()

    def _digest(self, text: str) -> str:
        return hashlib.sha256(self.salt + text.encode()).hexdigest()

    def fake_iban(self, real: str) -> str:
        key = account_key(real)
        if not key:
            return ""
        if key not in self.ibans:
            country = key[:2] if key[:2].isalpha() else "AT"
            length = max(len(key) - 4, 11) if normalise_iban(key) else 16
            digits = str(int(self._digest(key), 16))[:length].rjust(length, "0")
            check = 98 - int("".join(str(int(c, 36)) for c in digits + country + "00")) % 97
            self.ibans[key] = f"{country}{check:02d}{digits}"
        return self.ibans[key]

    def fake_number(self, real: str) -> str:
        if real not in self.numbers:
            digits = str(int(self._digest("n" + real), 16))
            self.numbers[real] = (digits * 3)[: len(real)]
        return self.numbers[real]

    def person(self, name: str) -> str:
        key = collapse(name).lower()
        self.spelling.setdefault(key, collapse(name))
        if key not in self.people:
            # Letters, not digits: the payee clean-up removes numbers at the
            # end of a name, so "Person 01" and "Person 02" would be one payee.
            n = len(self.people)
            self.people[key] = f"Person {chr(65 + n // 26 % 26)}{chr(97 + n % 26)}"
        return self.people[key]

    def name(self, name: str) -> str:
        clean = collapse(name)
        if not clean:
            return ""
        key = clean.lower()
        if key in self.holder_names:
            return HOLDER_PSEUDONYM
        if key in self.rename:
            return self.rename[key]
        if key in self.people:  # replaced once: replaced in every spelling
            return self.person(clean)
        if key not in self.force_replace and is_company(clean, self.keep):
            self.kept.add(clean)
            return clean
        return self.person(clean)

    def learn_names(self, header, rows):
        """Collect every name before any text is rewritten, so a person's name
        is also replaced inside booking texts and purposes."""
        col = {norm_header(h): i for i, h in enumerate(header)}
        for r in rows:
            for field in ("auftraggebername", "empfaengername"):
                if field in col and collapse(r[col[field]]):
                    self.name(r[col[field]])
            for field in ("buchungstext", "verwendungszweck"):
                if field in col:
                    for found in _NAME_IN_TEXT.findall(collapse(r[col[field]])):
                        if len(found.split()) >= 2:
                            self.name(found)

    def text(self, value) -> str:
        t = collapse(value)
        if not t:
            return t
        markers: list[str] = []

        def protect(fake: str) -> str:
            markers.append(fake)
            return f"\x00{len(markers) - 1}\x00"

        t = _IBAN_IN_TEXT.sub(lambda m: protect(self.fake_iban(m.group(0))), t)
        for real in sorted(self.rename, key=len, reverse=True):
            t = re.sub(re.escape(real), lambda _m, f=self.rename[real]: protect(f), t, flags=re.I)
        for real in sorted(self.holder_names | set(self.people) | self.force_replace, key=len, reverse=True):
            if real:
                fake = HOLDER_PSEUDONYM if real in self.holder_names else self.person(real)
                t = re.sub(re.escape(real), lambda _m, f=fake: protect(f), t, flags=re.I)
        t = _DATE_IN_TEXT.sub(lambda m: protect(self.shift_text_date(m)), t)
        t = _CARD_AMOUNT.sub(lambda m: m.group(1) + protect(self.scale_text_amount(m.group(2))), t)
        t = _LONG_NUMBER.sub(lambda m: self.fake_number(m.group(0)), t)
        return re.sub(r"\x00(\d+)\x00", lambda m: markers[int(m.group(1))], t)

    def shift_text_date(self, m) -> str:
        try:
            day = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return m.group(0)
        return (day + datetime.timedelta(days=self.offset)).strftime("%d.%m.%Y")

    def scale_text_amount(self, text: str) -> str:
        try:
            value = Decimal(text.replace(".", "").replace(",", "."))
        except Exception:  # noqa: BLE001
            return text
        scaled = (value * self.factor).quantize(Decimal("0.01"), ROUND_HALF_UP)
        return f"{scaled:.2f}".replace(".", ",")

    def amount(self, value):
        if isinstance(value, float):
            scaled = (Decimal(repr(value)) * self.factor).quantize(Decimal("0.01"), ROUND_HALF_UP)
            return float(scaled)
        return self.text(value)

    def date(self, value):
        if isinstance(value, float):
            return value + self.offset
        return self.text(value)

    def row(self, header, cells):
        out = []
        for h, value in zip(header, cells):
            field = norm_header(h)
            if field in ("buchungsdatum", "valutadatum"):
                out.append(self.date(value))
            elif field == "betrag":
                out.append(self.amount(value))
            elif field in ("auftraggeberkonto", "empfaengerkonto"):
                out.append(self.fake_iban(value) if collapse(value) else "")
            elif field in ("auftraggebername", "empfaengername"):
                out.append(self.name(value))
            elif field == "waehrung":
                out.append(value)
            else:
                out.append(self.text(value))
        return out


# --------------------------------------------------------------- selecting --
def select(rows, header, target: int, rng: random.Random):
    col = {norm_header(h): i for i, h in enumerate(header)}
    groups = defaultdict(list)
    for index, r in enumerate(rows):
        groups[kind(r[col["buchungstext"]], r[col["betrag"]])].append(index)
    chosen: list[int] = []
    for indices in groups.values():
        chosen += rng.sample(indices, min(PER_KIND, len(indices)))
    rest = [i for i in range(len(rows)) if i not in set(chosen)]
    if len(chosen) < target:
        chosen += rng.sample(rest, min(target - len(chosen), len(rest)))
    if len(chosen) > target:
        # Keep one of every kind first, then fill up to the target.
        firsts = {kind(rows[i][col["buchungstext"]], rows[i][col["betrag"]]): i for i in chosen}
        others = [i for i in chosen if i not in set(firsts.values())]
        chosen = list(firsts.values()) + others[: max(0, target - len(firsts))]
    return [rows[i] for i in sorted(chosen, key=lambda i: (rows[i][col["buchungsdatum"]], i))]


# ----------------------------------------------------------------- writing --
def card_merchants(header, rows) -> list[str]:
    """The payee the importer will make of every row without name columns —
    the merchants of card payments, exactly as they will be published."""
    col = {norm_header(h): i for i, h in enumerate(header)}
    found = set()
    for r in rows:
        names = [collapse(r[col[f]]) for f in ("auftraggebername", "empfaengername") if f in col]
        if not any(names) and "buchungstext" in col:
            found.add(ba.clean_payee(r[col["buchungstext"]]))
    return sorted(found)


def write(out: Path, header, rows, anonymiser, report_lines):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "rows.jsonl", "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"amount_factor": str(anonymiser.factor),
                                 "date_offset_days": anonymiser.offset}) + "\n")
        handle.write(json.dumps({"header": header}, ensure_ascii=False) + "\n")
        for r in rows:
            handle.write(json.dumps({"cells": r}, ensure_ascii=False) + "\n")
    book = xlwt.Workbook()
    sheet = book.add_sheet("Umsatzliste")
    for c, value in enumerate(header):
        sheet.write(0, c, value)
    for i, r in enumerate(rows, start=1):
        for c, value in enumerate(r):
            sheet.write(i, c, value)
    book.save(str(out / "sample.xls"))
    with open(out / "preview.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)
    review = report_lines + [
        "",
        f"Names KEPT as companies or merchants ({len(anonymiser.kept)}) — check that no person is here:",
        *[f"  {n}" for n in sorted(anonymiser.kept)],
        "",
        f"Companies RENAMED ({len(anonymiser.renamed)}):",
        *[f"  {real}  ->  {new}" for real, new in sorted(anonymiser.renamed.items())],
        "",
        f"Names REPLACED ({len(anonymiser.people) + len(anonymiser.holder_names)}):",
        *[f"  {anonymiser.spelling.get(n, n)}  ->  {HOLDER_PSEUDONYM}" for n in sorted(anonymiser.holder_names)],
        *[f"  {anonymiser.spelling.get(n, n)}  ->  {p}"
          for n, p in sorted(anonymiser.people.items(), key=lambda x: x[1])],
        "",
        f"Merchants in card texts ({len(card_merchants(header, rows))}) — these stay as they are; "
        "check that none is a person (add it to --names replace):",
        *[f"  {m}" for m in card_merchants(header, rows)],
        "",
        "This file names real people. Do NOT commit it.",
    ]
    (out / "review.txt").write_text("\n".join(review) + "\n", encoding="utf-8")


# --------------------------------------------------------------------- run --
def run(paths, out: Path, seed: int | None = None, target: int = DEFAULT_TARGET, names=None):
    seed = seed if seed is not None else random.SystemRandom().randrange(1 << 30)
    names = names or {}
    anonymiser = Anonymiser(seed, names.get("keep", ()), names.get("replace", ()), names.get("rename"))
    header, all_rows, report = None, [], ["Real-data check with the Bank Austria importer:"]
    own_counter: Counter = Counter()
    for path in paths:
        path = Path(path)
        result = ba.read_rows(path.read_bytes(), filename=path.name)
        own_counter[result.own_iban] += 1
        reasons = Counter(s["reason"] for s in result.skipped)
        report.append(f"  {path.name}: {len(result.rows)} rows read, {len(result.skipped)} skipped"
                      + (f" ({dict(reasons)})" if reasons else ""))
        file_header, rows, _ = load(path)
        if header is None:
            header = file_header
        elif file_header != header:
            raise SystemExit(f"{path.name}: other columns than the first file.\n  {file_header}\n  {header}")
        all_rows += rows
    if len(own_counter) > 1:
        report.append(f"  WARNING: the files disagree on the own IBAN ({len(own_counter)} different).")
    own = own_counter.most_common(1)[0][0]
    col = {norm_header(h): i for i, h in enumerate(header)}
    holder = Counter()
    for r in all_rows:
        for name_f, acct_f in (("auftraggebername", "auftraggeberkonto"), ("empfaengername", "empfaengerkonto")):
            if name_f in col and acct_f in col and account_key(r[col[acct_f]]) == own and collapse(r[col[name_f]]):
                holder[collapse(r[col[name_f]]).lower()] += 1
                anonymiser.spelling.setdefault(collapse(r[col[name_f]]).lower(), collapse(r[col[name_f]]))
    anonymiser.holder_names = set(holder)
    anonymiser.learn_names(header, all_rows)
    sample = select(all_rows, header, target, anonymiser.rng)
    fake = [anonymiser.row(header, r) for r in sample]
    report.append(f"  {len(all_rows)} rows in total, {len(fake)} in the sample.")
    write(Path(out), header, fake, anonymiser, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path, help="Bank Austria .xls exports")
    parser.add_argument("--out", type=Path, default=Path("tmp/anonymized"), help="output folder (git-ignored)")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET, help="rows in the sample")
    parser.add_argument("--seed", type=int, help="fix the random choices (default: new each run)")
    parser.add_argument("--names", type=Path,
                        help='JSON: {"keep": [...], "replace": [...], "rename": {"Company": "NEUTRAL GMBH"}}')
    args = parser.parse_args(argv)
    names = json.loads(args.names.read_text(encoding="utf-8")) if args.names else None
    report = run(args.files, args.out, seed=args.seed, target=args.target, names=names)
    print("\n".join(report))
    print(f"\nWritten to {args.out}/. Review preview.csv and review.txt before you copy anything.")


if __name__ == "__main__":
    main()
