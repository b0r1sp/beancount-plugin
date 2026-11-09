"""Statements by reference, proposals by id.

WHY THIS EXISTS
---------------
Parsing is deterministic (``importing.py``), but handing its full answer back
to the model means the statement travels through the model, and that cannot
complete for a real statement:

* **Out:** the tool returned every row as JSON (253 KB for 401 rows), and the
  only way into the book was for the model to RE-TYPE all of them as beancount
  into ``add_transactions``. That is a model's transcription standing between a
  deterministic parser and the ledger, which is exactly what ``importing.py``
  exists to remove — bean-check does not catch a mistyped amount whose postings still
  balance. And the 253 KB stayed in the session, re-sent on every later turn.

So the bytes and the rows stay on disk. The statement is STAGED once
and named by an id; ``propose`` runs from those bytes and the full result is
STORED under a proposal id; the model is shown a bounded summary — counts, the
column mapping, what was flagged, and each distinct payee once — and answers
with a ``{payee → account}`` map. ``commit`` renders the transactions from the
stored proposal, not from anything the model typed, and writes them through the
same ``add_directives`` gate as every other write: bean-check, then git.

WHERE IT LIVES
--------------
``<book>/.git/beancount-imports/``. Inside ``.git`` for the reason ``ledger.py``
already keeps its write lock there: anything in the book directory proper shows
up in ``git status`` and in the next ``git add``, and a statement
must never be committed into the ledger history by accident.

Every caller goes through ``stage`` / ``load_statement`` / ``save_proposal``
here, so moving the store is a change to this module and not to its callers.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from . import importing
from .ledger import LedgerError

# The date an account opened BY AN IMPORT is opened on.
#
# 🔴 NOT THE IMPORT'S EARLIEST ROW. It used to be, and statements do not arrive
# in date order: import September first (opening Expenses:Groceries on
# 1 Sep), then August, and every August row on that account fails bean-check
# as "inactive account" — the whole import refused. Measured 2026-10-02 (the
# overnight scheduled-messages experiment had to pre-open every account to get
# through a nine-month import). Dated like Equity:Opening-Balances and
# Expenses:Rounding (ledger.py), so it is open before any row a user can have.
AUTO_OPEN_DATE = "1970-01-01"


STORE_DIRNAME = "beancount-imports"

# The largest statement the store accepts. Generous, because bytes on disk cost
# nothing a model is billed for.
MAX_STATEMENT_BYTES = 8 * 1024 * 1024

# How many distinct payees the summary lists. Each is ~120 bytes of JSON, so the
# whole list stays in single-digit kilobytes; the rest are counted, not shown,
# and stay on their placeholder account until categorised.
PAYEE_LIMIT = 100
# How many flagged rows are shown individually. The REASONS are always counted
# in full; this bounds only the examples.
FLAGGED_SAMPLE = 10
# How many proposals, and how many statements, a book keeps. Older ones are
# deleted on the next save. Statements are kept here only until they are
# imported, so a bounded window is enough; the ledger keeps each row's
# import-id, which is what a re-import needs, not the file.
KEEP_PROPOSALS = 50
KEEP_STATEMENTS = 50

_STATEMENT_ID_RE = re.compile(r"^stm_[0-9a-f]{16}$")
_PROPOSAL_ID_RE = re.compile(r"^prp_[0-9a-f]{12}$")
_ACCOUNT_RE = re.compile(r"^(Assets|Liabilities|Equity|Income|Expenses)(:[A-Z0-9][A-Za-z0-9-]*)+$")
SKIP = "skip"


class ProposalError(LedgerError):
    """A stored statement or proposal cannot be used, and the book is untouched."""


# ------------------------------------------------------------------ the store --
def store_root(book_root) -> Path:
    return Path(book_root) / ".git" / STORE_DIRNAME


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_last_stamp_ns = 0


def _write_atomic(path: Path, data: bytes) -> None:
    """Write-then-rename, and stamp an mtime strictly later than the last one.

    ⚠️ THE STAMP IS WHAT MAKES PRUNING DETERMINISTIC. `_prune` keeps the newest
    records by mtime, and a filesystem's own clock is coarse — Linux stamps
    files from a tick that can be milliseconds wide, so two records written in
    one tick tie and "the oldest" becomes whichever the directory lists first.
    Setting it explicitly, from a clock that only moves forward, keeps the
    newest record the one that was written last.
    """
    global _last_stamp_ns
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{secrets.token_hex(4)}")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    _last_stamp_ns = max(time.time_ns(), _last_stamp_ns + 1_000)
    os.utime(path, ns=(_last_stamp_ns, _last_stamp_ns))


def content_to_bytes(content, content_encoding: str = "auto") -> bytes:
    """The caller's ``content`` argument as the bytes it stands for.

    Mirrors ``importing.decode_content``'s rule for telling base64 from a
    pasted statement — the base64 alphabet contains no delimiter — so a
    statement staged here decodes to the same text the importer would have read
    from the argument directly.
    """
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    if not isinstance(content, str) or not content.strip():
        raise ProposalError("content is empty — there is no statement to store.")
    choice = (content_encoding or "auto").strip().lower()
    packed = "".join(content.split())
    if choice == "base64" or (choice == "auto" and importing._looks_like_base64(content)):
        try:
            return base64.b64decode(packed, validate=True)
        except (binascii.Error, ValueError):
            if choice == "base64":
                raise ProposalError(
                    "content_encoding='base64' but the content is not valid base64."
                ) from None
    return content.encode("utf-8")


def read_statement_file(file_path: str) -> tuple[bytes, str]:
    """The bytes of a statement file on this machine, and its file name.

    The server reads the file itself, so the model does not have to copy its
    contents into the tool call — which fails for a file of a few hundred KB,
    because one model response cannot hold it. The size is checked BEFORE the
    file is read.
    """
    target = Path(str(file_path).strip()).expanduser()
    if not target.is_absolute():
        raise ProposalError(
            f"'{file_path}' is not a full path. Give the full path of the "
            f"statement file, for example {Path.home() / 'Downloads' / 'statement.csv'}."
        )
    if not target.exists():
        raise ProposalError(f"No file at {target}.")
    if target.is_dir():
        raise ProposalError(f"{target} is a directory, not a statement file.")
    size = target.stat().st_size
    if size > MAX_STATEMENT_BYTES:
        raise ProposalError(
            f"{target.name} is {size:,} bytes; the limit is "
            f"{MAX_STATEMENT_BYTES:,}. Export a shorter date range."
        )
    try:
        return target.read_bytes(), target.name
    except OSError as exc:
        raise ProposalError(f"Could not read {target}: {exc}") from None


def stage_statement(book_root, data: bytes, filename: str = "") -> dict:
    """Store a statement's bytes on the book and return the reference to it.

    Content-addressed: the id is the first 64 bits of the SHA-256, so staging
    the same file twice returns the same id and stores it once. The FULL hash is
    kept beside the bytes and re-checked on every load, because the id is short
    enough for a model to copy exactly and a short id must not be what vouches
    for the content.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ProposalError("The statement is empty — there is nothing to store.")
    if len(data) > MAX_STATEMENT_BYTES:
        raise ProposalError(
            f"The statement is {len(data):,} bytes; the limit is "
            f"{MAX_STATEMENT_BYTES:,}. Export a shorter date range."
        )
    digest = hashlib.sha256(data).hexdigest()
    statement_id = f"stm_{digest[:16]}"
    folder = store_root(book_root) / "statements"
    meta_path = folder / f"{statement_id}.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if meta.get("sha256") != digest:
            # Two different files sharing 64 bits of hash. Astronomically
            # unlikely, and refused rather than overwritten if it ever happens.
            raise ProposalError(
                f"A different statement is already stored as {statement_id}. "
                f"Nothing was stored."
            )
        return _public_statement(meta)
    meta = {
        "statement_id": statement_id,
        "sha256": digest,
        "bytes": len(data),
        # Recorded for the humans reading a proposal and for sniffing, never as
        # a path: the id is the only thing that ever names a file here.
        "filename": _clean_filename(filename),
        "staged_at": _now(),
    }
    _write_atomic(folder / f"{statement_id}.bin", bytes(data))
    _write_atomic(meta_path, json.dumps(meta).encode())
    _prune(folder, "stm_*.json", KEEP_STATEMENTS, (".bin",))
    return _public_statement(meta)


def _clean_filename(name: str) -> str:
    name = "".join(ch for ch in (name or "") if ch.isprintable())
    return name.replace("/", "_").replace("\\", "_").strip()[:120]


def _public_statement(meta: dict) -> dict:
    return {k: meta[k] for k in ("statement_id", "sha256", "bytes", "filename")}


def load_statement(book_root, statement_id: str) -> tuple[bytes, dict]:
    statement_id = (statement_id or "").strip()
    if not _STATEMENT_ID_RE.match(statement_id):
        raise ProposalError(
            f"{statement_id!r} is not a statement id. It looks like "
            f"'stm_' followed by 16 hex characters, and it is given to you when "
            f"the statement is stored."
        )
    folder = store_root(book_root) / "statements"
    try:
        meta = json.loads((folder / f"{statement_id}.json").read_text())
        data = (folder / f"{statement_id}.bin").read_bytes()
    except FileNotFoundError:
        raise ProposalError(
            f"No statement {statement_id} is stored on this book. Ask the "
            f"user to send the file again."
        ) from None
    if hashlib.sha256(data).hexdigest() != meta.get("sha256"):
        raise ProposalError(
            f"Statement {statement_id} no longer matches the hash it was stored "
            f"under, so it will not be read. Ask the user to send it again."
        )
    return data, meta


def _proposal_path(book_root, proposal_id: str) -> Path:
    proposal_id = (proposal_id or "").strip()
    if not _PROPOSAL_ID_RE.match(proposal_id):
        raise ProposalError(
            f"{proposal_id!r} is not a proposal id. It looks like 'prp_' "
            f"followed by 12 hex characters, and propose_transactions returns it."
        )
    return store_root(book_root) / "proposals" / f"{proposal_id}.json"


def load_proposal(book_root, proposal_id: str) -> dict:
    path = _proposal_path(book_root, proposal_id)
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise ProposalError(
            f"No proposal {proposal_id} exists on this book. Proposals are kept "
            f"for the last {KEEP_PROPOSALS} imports; call propose_transactions "
            f"again to make a new one."
        ) from None


def _save_proposal(book_root, record: dict) -> None:
    path = _proposal_path(book_root, record["proposal_id"])
    _write_atomic(path, json.dumps(record).encode())
    _prune(path.parent, "prp_*.json", KEEP_PROPOSALS, (".claim",))


def _prune(folder: Path, pattern: str, keep: int, companions: tuple[str, ...]) -> None:
    """Delete all but the ``keep`` newest records, with their companion files."""
    records = sorted(folder.glob(pattern), key=lambda p: (p.stat().st_mtime_ns, p.name))
    for old in records[:-keep] if keep else records:
        for leftover in (old, *(old.with_suffix(ext) for ext in companions)):
            try:
                leftover.unlink()
            except FileNotFoundError:
                pass


# -------------------------------------------------------------------- payees --
def payee_key(narration: str) -> str:
    """The part of a narration that names WHO, with the per-transaction noise gone.

    Card narrations carry a store number, a terminal id or a reference on every
    line — ``STARBUCKS #4412 SEATTLE WA``, ``AMZN MKTP US*2K4LQ`` — so grouping
    by the raw text would show the model the same merchant once per purchase and
    make it categorise each one. Any word containing a digit is dropped, and
    punctuation-only words with it; what is left is uppercased.

    Deterministic, and applied identically at propose and at commit, so a key
    the model is shown is exactly a key the commit will match.
    """
    words = []
    for word in (narration or "").upper().split():
        if any(ch.isdigit() for ch in word):
            continue
        word = word.strip("#*/.,;:-_'\"()[]")
        if word:
            words.append(word)
    key = " ".join(words)[:60].strip()
    return key or " ".join((narration or "").upper().split())[:60] or "(NO DESCRIPTION)"


def group_key(row: dict) -> str:
    """Which payee group a proposed row belongs to.

    The counterparty's IBAN when the statement has one: a name is spelled
    differently from one booking to the next ("REWE Markt GmbH", "REWE SAGT
    DANKE 4711"), an IBAN is not. Without an IBAN, the counterparty's name;
    without that, the narration — both through ``payee_key``.
    """
    iban = row.get("counterparty_iban") or ""
    if iban:
        return iban
    return payee_key(row.get("payee") or row["narration"])


def override_key(key: str) -> str:
    """Normalise a key the model sent in overrides.payees the same way
    ``group_key`` built it: an IBAN loses its spaces, a name goes through
    ``payee_key``."""
    return importing.normalise_iban(key) or payee_key(key)


# ------------------------------------------------------------------- propose --
def propose(book_root, book_text: str, statement_id: str, account: str, **options) -> dict:
    """Run the importer over a STAGED statement, store the result, summarise it.

    Returns the summary a model is shown — never the rows. The full importer
    output is kept on the book under the returned ``proposal_id``.
    """
    data, meta = load_statement(book_root, statement_id)
    result = importing.propose(
        data, account, book_text=book_text, filename=meta.get("filename", ""), **options,
    )
    result.pop("beancount", None)   # re-rendered at commit, from the rows
    proposal_id = f"prp_{secrets.token_hex(6)}"
    record = {
        "proposal_id": proposal_id,
        "created_at": _now(),
        "statement": _public_statement(meta),
        "account": result["account"],
        "options": {k: v for k, v in options.items() if v not in ("", None, "auto", {})},
        "result": result,
        "committed": None,
    }
    _save_proposal(book_root, record)
    return summarise(record)


def _money(values: dict) -> dict:
    return {ccy: importing._fmt(v) for ccy, v in sorted(values.items())}


def summarise(record: dict) -> dict:
    """What the model is shown about a proposal: bounded, whatever the row count.

    ⚠️ BOUNDED IS THE REQUIREMENT, NOT A NICETY. Whatever this returns is a tool
    result, and clients keep tool results in the session and re-send them on
    every later turn — so a result that grows with the statement becomes a
    permanent per-message cost. The size is fixed by the limits at the top of
    this file.
    """
    result = record["result"]
    rows = result["proposed"]
    totals_in: dict = defaultdict(Decimal)
    totals_out: dict = defaultdict(Decimal)
    payees: dict = {}
    reasons: Counter = Counter()
    flagged = []
    for row in rows:
        amount = Decimal(row["amount"])
        (totals_in if amount > 0 else totals_out)[row["currency"]] += amount
        key = group_key(row)
        entry = payees.setdefault(key, {
            "payee": key, "rows": 0, "net": defaultdict(Decimal),
            "example": row["narration"][:80], "names": Counter(),
            "iban": row.get("counterparty_iban") or "",
        })
        entry["rows"] += 1
        entry["net"][row["currency"]] += amount
        if row.get("payee"):
            entry["names"][row["payee"][:60]] += 1
        if row["flag"] == "!":
            for reason in row["ambiguities"]:
                reasons[reason[:240]] += 1
            if len(flagged) < FLAGGED_SAMPLE:
                flagged.append({
                    "import_id": row["import_id"], "date": row["date"],
                    "amount": row["amount"], "currency": row["currency"],
                    "narration": row["narration"][:80],
                    "reason": (row["ambiguities"] or [""])[0][:240],
                })

    ranked = sorted(
        payees.values(),
        key=lambda p: (-p["rows"], -max((abs(v) for v in p["net"].values()), default=0), p["payee"]),
    )
    listed = []
    for p in ranked[:PAYEE_LIMIT]:
        item = {"payee": p["payee"], "rows": p["rows"], "net": _money(p["net"])}
        if p["iban"]:
            item["iban"] = p["iban"]
        if p["names"]:
            # Several names on one IBAN is normal for a payment provider
            # (PayPal, Klarna): say so, so those rows can be split by row.
            item["names"] = [n for n, _ in p["names"].most_common(3)]
            if len(p["names"]) > 3:
                item["names_not_listed"] = len(p["names"]) - 3
        if p["example"].upper() != p["payee"]:
            item["example"] = p["example"]
        listed.append(item)

    dates = [r["date"] for r in rows]
    source = result["source"]
    counts = dict(result["counts"])
    committed = record.get("committed")
    return {
        "proposal_id": record["proposal_id"],
        "wrote_anything": bool(committed),
        "committed": committed,
        "statement": record["statement"],
        "account": result["account"],
        "currency": result["currency"],
        "counts": counts,
        "date_range": {"from": min(dates), "to": max(dates)} if dates else None,
        "totals": {"money_in": _money(totals_in), "money_out": _money(totals_out)},
        "mapping": result["mapping"],
        "source": {
            "format": source["format"],
            "rows_parsed": source["rows_parsed"],
            "rows_skipped": len(source["rows_skipped"]),
            "skipped_sample": source["rows_skipped"][:5],
        },
        "already_in_book": counts.get("skipped_as_already_imported", 0),
        "flagged": {
            "rows": counts.get("flagged_for_review", 0),
            "reasons": [{"reason": r, "rows": n} for r, n in reasons.most_common(8)],
            "sample": flagged,
        },
        "payees": listed,
        "payees_not_listed": max(0, len(ranked) - PAYEE_LIMIT),
        "accounts_to_open": result["accounts_to_open"],
        "notes": result["notes"],
        "next_step": (
            "Nothing is written yet. Tell the user the counts, the date range "
            "and anything flagged. Then choose an account for each payee and call "
            "commit_proposal with this proposal_id and overrides like "
            '{"payees": {"<payee>": "Expenses:Groceries"}}, using each payee '
            "value exactly as listed — an IBAN when the statement has one, so "
            "a counterparty is recognised however its name is spelled. An IBAN "
            "with several names (a payment provider such as PayPal) may need "
            "per-row accounts. Payees you leave out "
            "stay on Expenses:Unclassified / Income:Unclassified. To change or "
            'drop one row, use {"rows": {"<import_id>": "<account>" or "skip"}}. '
            "Do NOT re-type the transactions into add_transactions — the commit "
            "writes them from the parsed rows."
            if rows else
            "There is nothing new to write: every row in this statement is "
            "already in the book."
        ),
    }


# -------------------------------------------------------------------- commit --
def parse_overrides(overrides) -> tuple[dict, dict]:
    """``overrides`` as ``(payees, rows)``: JSON text or an already-parsed dict."""
    if overrides in (None, "", {}):
        return {}, {}
    if isinstance(overrides, str):
        try:
            overrides = json.loads(overrides)
        except json.JSONDecodeError as exc:
            raise ProposalError(
                f'overrides must be a JSON object like {{"payees": {{"REWE": '
                f'"Expenses:Groceries"}}}} — {exc}'
            ) from None
    if not isinstance(overrides, dict):
        raise ProposalError("overrides must be a JSON object, not a list or a scalar.")
    unknown = set(overrides) - {"payees", "rows"}
    if unknown:
        raise ProposalError(
            f"overrides takes 'payees' and 'rows' only, not {sorted(unknown)}."
        )
    payees, rows = overrides.get("payees") or {}, overrides.get("rows") or {}
    if not isinstance(payees, dict) or not isinstance(rows, dict):
        raise ProposalError("overrides.payees and overrides.rows must be JSON objects.")
    bad = [
        v for v in list(payees.values()) + list(rows.values())
        if not isinstance(v, str) or not (v == SKIP or _ACCOUNT_RE.match(v))
    ]
    if bad:
        raise ProposalError(
            f"{bad[:3]} {'is' if len(bad) == 1 else 'are'} not a beancount "
            f"account name (or 'skip'). Accounts start with Assets:, "
            f"Liabilities:, Equity:, Income: or Expenses:, and each part starts "
            f"with a capital letter or a digit. Nothing was written."
        )
    return {override_key(k): v for k, v in payees.items()}, dict(rows)


def commit(ledger, proposal_id: str, overrides=None, open_new_accounts: bool = False) -> dict:
    """Write a stored proposal to the book — bean-check, then git, like every write.

    The transactions are rendered HERE from the parsed rows. The model's only
    contribution is which account each payee (or row) goes to; it never types a
    date or an amount, so it cannot mistype one.

    Idempotent against the book rather than against the proposal alone: rows
    whose ``import-id`` has reached the ledger since the proposal was made — an
    overlapping import, a retried commit — are skipped, not written twice.
    """
    root = ledger.root
    record = load_proposal(root, proposal_id)
    if record.get("committed"):
        raise ProposalError(
            f"Proposal {proposal_id} was already committed as "
            f"{record['committed']['commit']}. Nothing was written again."
        )
    payees, row_overrides = parse_overrides(overrides)
    rows = record["result"]["proposed"]
    known_ids = {r["import_id"] for r in rows}
    stray = sorted(set(row_overrides) - known_ids)
    if stray:
        raise ProposalError(
            f"overrides.rows names {stray[:3]}, which {'is' if len(stray) == 1 else 'are'} "
            f"not in proposal {proposal_id}. Use the import_id values the proposal "
            f"showed. Nothing was written."
        )

    book_text = ledger.read_all()
    in_book = importing.existing_import_ids(book_text)
    opens = importing.account_currencies(book_text)

    chosen: list[importing.Proposal] = []
    skipped_in_book = skipped_by_you = unclassified = 0
    for row in rows:
        if row["import_id"] in in_book:
            skipped_in_book += 1
            continue
        target = (
            row_overrides.get(row["import_id"])
            or payees.get(group_key(row))
            # A row grouped by IBAN can still be named by its counterparty.
            or (payees.get(payee_key(row["payee"])) if row.get("payee") else None)
        )
        if target == SKIP:
            skipped_by_you += 1
            continue
        counter = target or row["counter_account"]
        if counter in (importing.EXPENSE_PLACEHOLDER, importing.INCOME_PLACEHOLDER):
            unclassified += 1
        chosen.append(importing.Proposal(
            date=row["date"], flag=row["flag"], narration=row["narration"],
            amount=row["amount"], currency=row["currency"], account=row["account"],
            counter_account=counter, import_id=row["import_id"],
            source_row=row["source_row"], ambiguities=list(row["ambiguities"]),
            payee=row.get("payee", ""), counterparty_iban=row.get("counterparty_iban", ""),
        ))
    if not chosen:
        raise ProposalError(
            f"Nothing to write from proposal {proposal_id}: "
            f"{skipped_in_book} row(s) are already in the book and "
            f"{skipped_by_you} were skipped by overrides."
        )

    needed = sorted({p.account for p in chosen} | {p.counter_account for p in chosen})
    missing = [a for a in needed if a not in opens]
    opened: list[str] = []
    prefix = ""
    if missing:
        if not open_new_accounts:
            raise ProposalError(
                f"These accounts are not open in the book yet: {missing}. Check "
                f"the names with the user, then call commit_proposal again "
                f"with open_new_accounts=true to open them in the same commit. "
                f"Nothing was written."
            )
        currencies = sorted({p.currency for p in chosen})
        lines = []
        for account in missing:
            # The statement's own account is pinned to its currency when that is
            # unambiguous; counter-accounts are left open to any currency, since
            # pinning an expense category is the trap CHART in the tests names.
            pin = f" {currencies[0]}" if account == record["account"] and len(currencies) == 1 else ""
            lines.append(f"{AUTO_OPEN_DATE} open {account}{pin}")
        prefix = "\n".join(lines) + "\n\n"
        opened = missing

    text = prefix + "\n\n".join(p.to_beancount() for p in chosen)
    name = record["statement"].get("filename") or record["statement"]["statement_id"]
    message = f"Import {len(chosen)} transactions from {name} ({proposal_id})"

    # One commit per proposal, even under two concurrent callers: the claim is an
    # O_EXCL create, so exactly one of them proceeds. It is released on failure
    # so a rejected commit can be corrected and retried.
    claim = _proposal_path(root, proposal_id).with_suffix(".claim")
    try:
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        raise ProposalError(
            f"Proposal {proposal_id} is already being committed. Nothing was written."
        ) from None
    try:
        result = ledger.add_directives(text, message)
    except BaseException:
        claim.unlink(missing_ok=True)
        raise
    record["committed"] = {"commit": result.commit, "at": _now()}
    _save_proposal(root, record)
    return {
        "commit": result.commit,
        "message": result.message,
        "proposal_id": proposal_id,
        "written": len(chosen),
        "flagged": sum(1 for p in chosen if p.flag == "!"),
        "still_unclassified": unclassified,
        "skipped_already_in_book": skipped_in_book,
        "skipped_by_overrides": skipped_by_you,
        "opened_accounts": opened,
    }
