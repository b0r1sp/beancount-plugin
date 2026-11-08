"""Beancount MCP server.

Exposes a local, git-backed Beancount ledger (a "book") as MCP tools so an AI
agent can read and safely write double-entry books. Every mutating tool
validates with bean-check and commits to git; a rejected change never lands.

The book is a directory on this machine, chosen in this order:

1. ``$BEANCOUNT_BOOK``, if set;
2. the directory saved by ``set_book_location`` in ``~/.beancount/config.json``;
3. ``~/.beancount/main`` (on Windows, ``%USERPROFILE%\\.beancount\\main``).
"""
from __future__ import annotations

import json
import os
import sysconfig
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .ledger import Ledger, LedgerError
from . import assessment, instructions, proposals, receipts, reporting

BOOK_ENV = "BEANCOUNT_BOOK"

mcp = FastMCP("beancount", instructions=instructions.SERVER_INSTRUCTIONS)


def _tool(title: str, kind: str, *, destructive: bool, idempotent: bool) -> dict:
    """`@mcp.tool` arguments: a title and the MCP behaviour hints.

    Every tool carries all four hints, said rather than defaulted, because the
    spec's defaults are the cautious ones (a tool with no hints is assumed to
    write, destructively, to the open world).

    `kind` is "read" (never changes the book) or "write". `destructive` means
    the change takes something away that was there (revert); an append that
    bean-check validates is not destructive. The book is a closed system, so
    `openWorldHint` is false throughout.
    """
    read_only = kind == "read"
    if not read_only and kind != "write":  # pragma: no cover — a typo guard
        raise ValueError(f"kind must be 'read' or 'write', not {kind!r}")
    return {
        "title": title,
        "annotations": ToolAnnotations(
            title=title,
            readOnlyHint=read_only,
            destructiveHint=False if read_only else destructive,
            idempotentHint=idempotent,
            openWorldHint=False,
        ),
    }


CONFIG_KEY = "book"


def config_path() -> Path:
    """The plugin's settings file. Outside every book, so no book commits it."""
    return Path.home() / ".beancount" / "config.json"


def builtin_book_path() -> Path:
    return Path.home() / ".beancount" / "main"


def _read_config() -> dict:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise LedgerError(
            f"Cannot read {config_path()}: {e}. Fix or delete the file, or call "
            f"set_book_location again."
        ) from None
    return data if isinstance(data, dict) else {}


def _write_config(data: dict) -> None:
    target = config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _book_source() -> tuple[Path, str]:
    """The book directory and what chose it, in precedence order."""
    env = os.environ.get(BOOK_ENV, "").strip()
    if env:
        return Path(env).expanduser(), f"the environment variable {BOOK_ENV}"
    saved = str(_read_config().get(CONFIG_KEY, "")).strip()
    if saved:
        return Path(saved).expanduser(), f"set_book_location (saved in {config_path()})"
    return builtin_book_path(), "the default"


def default_book_path() -> Path:
    """Where the book lives: $BEANCOUNT_BOOK, else the saved location, else
    ~/.beancount/main. Resolved on every call, so a change applies at once."""
    return _book_source()[0]


def _normalise_location(path: str) -> Path:
    """An absolute, user-expanded directory path, or a LedgerError saying why not.

    Relative paths are refused: they would resolve against whatever directory
    the server happened to start in, which the user cannot see.
    """
    candidate = Path(path.strip().strip('"').strip("'")).expanduser()
    if not candidate.is_absolute():
        raise LedgerError(
            f"'{path}' is not a full path. Give the full path of a directory, "
            f"for example {Path.home() / 'Documents' / 'books'}."
        )
    if candidate.exists() and not candidate.is_dir():
        raise LedgerError(f"{candidate} is a file, not a directory.")
    return candidate


def _book() -> Ledger:
    """The local book. Resolved per call, so a new location applies at once."""
    return Ledger(default_book_path())


def _require(ledger: Ledger) -> Ledger:
    if not ledger.exists():
        raise LedgerError(
            f"No book at {ledger.root}. Create one with `create_book` "
            f"(or the /beancount:init command)."
        )
    return ledger


# --------------------------------------------------------------- lifecycle ---
@mcp.tool(**_tool("Where is the book", 'read', destructive=False, idempotent=True))
def book_location() -> str:
    """Show which local directory this plugin reads and writes, and why."""
    root, source = _book_source()
    state = "exists" if Ledger(root).exists() else "does not exist yet — call create_book"
    return f"Book directory: {root}\nChosen by: {source}\nState: {state}"


@mcp.tool(**_tool("Choose where the book is", 'write', destructive=False, idempotent=True))
def set_book_location(path: str) -> str:
    """Choose the local directory of the book. Every tool uses it from the next call.

    `path` is the FULL path of a directory, e.g. '/Users/ana/Documents/books'
    or 'D:\\finance\\books'. `~` is expanded. The directory need not exist yet:
    if there is no book in it, call `create_book` next. Give an empty `path`
    to forget the choice and go back to the default (~/.beancount/main).

    The choice is saved in ~/.beancount/config.json. It does not move or copy
    an existing book. The environment variable BEANCOUNT_BOOK, if set, still
    wins over this setting.
    """
    config = _read_config()
    if not path.strip():
        config.pop(CONFIG_KEY, None)
        _write_config(config)
        chosen = builtin_book_path()
        head = f"Saved location removed. The book is now the default: {chosen}"
    else:
        chosen = _normalise_location(path)
        config[CONFIG_KEY] = str(chosen)
        _write_config(config)
        head = f"Book location set to {chosen} (saved in {config_path()})."
    lines = [head]
    if os.environ.get(BOOK_ENV, "").strip():
        lines.append(
            f"⚠️ {BOOK_ENV} is set to {os.environ[BOOK_ENV]} and overrides this "
            f"setting, so the tools still use that directory. Remove the variable "
            f"and restart to use the new location."
        )
        return "\n".join(lines)
    if Ledger(chosen).exists():
        lines.append("There is a book there. Every tool uses it now.")
    elif chosen.is_dir() and any(chosen.iterdir()):
        lines.append(
            "The directory exists but holds no book. Call create_book to make one "
            "there (it refuses rather than overwrite a file), or choose an empty "
            "directory."
        )
    else:
        lines.append("There is no book there yet. Call create_book to make one.")
    return "\n".join(lines)


@mcp.tool(**_tool("Create a local book", 'write', destructive=False, idempotent=False))
def create_book(name: str = "My Books", currency: str = "USD", path: str = "") -> str:
    """Initialise a new, empty book (a git-backed Beancount ledger) on this machine.

    `path` is optional: the full path of a local directory to create the book
    in. When given, it is also saved as the book location (as
    `set_book_location` does), so every other tool uses the new book. Without
    it, the book goes in the current location (see `book_location`).

    Safe to call once per book; errors if a book already exists at the location.
    """
    target = _normalise_location(path) if path.strip() else None
    ledger = Ledger(target) if target else _book()
    res = ledger.init(name=name, currency=currency)
    note = ""
    if target is not None:
        config = _read_config()
        config[CONFIG_KEY] = str(target)
        _write_config(config)
        note = f"\nSaved as the book location in {config_path()}."
        if os.environ.get(BOOK_ENV, "").strip():
            note += (f"\n⚠️ {BOOK_ENV} is set and overrides the saved location. "
                     f"Remove it and restart to use this book.")
    return f"Created book '{name}' ({currency}) at {ledger.root} [commit {res.commit}]{note}"


@mcp.tool(**_tool("Book summary", 'read', destructive=False, idempotent=True))
def book_status() -> str:
    """Summarise the current book: location, account count, balances, last commits."""
    ledger = _book()
    if not ledger.exists():
        return f"No book yet at {ledger.root}. Call create_book to start."
    accounts = ledger.accounts()
    hist = ledger.history(5)
    data = reporting.collect(ledger)
    t = data["totals"]
    lines = [
        f"Book: {data['title']} ({data['currency']}) at {ledger.root}",
        f"Accounts: {len(accounts)}",
        f"Net worth: {t['net_worth']:,.2f} {data['currency']}  "
        f"(assets {t['assets']:,.2f}, liabilities {t['liabilities']:,.2f})",
        # Year to date, not since inception — the label says which.
        f"Net income {data['period_start']} to {data['as_of']}: "
        f"{t['net_income']:,.2f} {data['currency']}",
        "Recent commits:",
        *[f"  {h['commit']} {h['date']} {h['message']}" for h in hist],
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- writes -----
@mcp.tool(**_tool("Record transactions", 'write', destructive=False, idempotent=False))
def add_transactions(beancount_text: str) -> str:
    """Append one or more transactions (raw Beancount syntax) to the book.

    The text is validated with bean-check and only committed if valid; on
    failure nothing is written and the validation errors are returned.
    Postings must balance. Open any new accounts first (open_accounts).
    """
    ledger = _require(_book())
    try:
        res = ledger.add_directives(beancount_text, "Add transactions via AI")
    except LedgerError as e:
        return f"REJECTED — ledger left unchanged.\n{e}"
    return f"Committed {res.commit}: {res.message}"


@mcp.tool(**_tool("Open accounts", 'write', destructive=False, idempotent=False))
def open_accounts(beancount_text: str) -> str:
    """Add account `open` (or close/commodity) directives to the book.

    Example: `2026-01-01 open Assets:Checking USD`
    """
    ledger = _require(_book())
    try:
        res = ledger.add_directives(beancount_text, "Open accounts via AI")
    except LedgerError as e:
        return f"REJECTED — ledger left unchanged.\n{e}"
    return f"Committed {res.commit}: {res.message}"


@mcp.tool(**_tool("Write ledger entries", 'write', destructive=False, idempotent=False))
def add_directives(beancount_text: str, message: str = "Update ledger via AI") -> str:
    """Append arbitrary Beancount directives (auto-routed by kind) and commit.

    Use for batch setup ("set up my whole situation"): opens, balances,
    transactions and prices in one validated commit.
    """
    ledger = _require(_book())
    try:
        res = ledger.add_directives(beancount_text, message)
    except LedgerError as e:
        return f"REJECTED — ledger left unchanged.\n{e}"
    return f"Committed {res.commit}: {res.message}"


# ---------------------------------------------------------------- reads ------
@mcp.tool(**_tool("List accounts", 'read', destructive=False, idempotent=True))
def list_accounts() -> str:
    """List every account currently open in the book."""
    return "\n".join(_require(_book()).accounts()) or "(no accounts yet)"


@mcp.tool(**_tool("Read the whole ledger", 'read', destructive=False, idempotent=True))
def get_ledger() -> str:
    """Return the full plain-text ledger (accounts, transactions, prices)."""
    return _require(_book()).read_all()


@mcp.tool(**_tool("Run a query", 'read', destructive=False, idempotent=True))
def run_query(bql: str) -> str:
    """Run a Beancount Query Language (BQL) query and return CSV-style rows.

    Example: `SELECT account, sum(position) WHERE account ~ 'Expenses' GROUP BY account`
    """
    rows = _require(_book()).query(bql)
    if not rows:
        return "(no rows)"
    return json.dumps(rows, indent=2)


@mcp.tool(**_tool("Account balances", 'read', destructive=False, idempotent=True))
def balances(account_filter: str = "") -> str:
    """Show balances grouped by account, optionally filtered by a regex.

    `account_filter` is a BQL regex like 'Assets' or 'Expenses:Food'.
    """
    where = f"WHERE account ~ '{account_filter}' " if account_filter else ""
    bql = f"SELECT account, sum(position) AS balance {where}GROUP BY account ORDER BY account"
    rows = _require(_book()).query(bql)
    if not rows:
        return "(no balances)"
    return "\n".join(f"{r.get('account',''):<40} {r.get('balance','')}" for r in rows)


# ------------------------------------------------------------- assessment ----
@mcp.tool(**_tool("Review the book", 'read', destructive=False, idempotent=True))
def assess_book() -> str:
    """Review the book and return COMPUTED facts about it as JSON.

    Coverage, monthly income/expense, cash, run rate and runway, category
    shares, month-over-month movers, unusually large postings, and data-quality
    flags.

    Read this and report it. Do NOT compute your own figures from it, do not
    extrapolate past the coverage window, and do not turn a `sufficient: false`
    into a number with a caveat — that field means the data cannot support the
    figure, and the honest answer is to say which data is missing.

    Every month is marked `complete`. Only complete months are averaged: a
    trailing partial month makes spending look like it fell in every category.
    """
    book = _require(_book())
    try:
        facts = assessment.assess(book)
    except LedgerError as e:
        return f"Could not assess the book: {e}"
    return json.dumps(facts, indent=2)


# --------------------------------------------------------------- importing ---
#
# Two tools. `propose_transactions` parses a statement and hands back a bounded
# summary plus a `proposal_id`; `commit_proposal` writes that proposal from the
# parsed rows. The model never carries the rows in either direction, so a
# statement's size is not limited by what a model can copy.
@mcp.tool(**_tool("Propose a statement import", 'write', destructive=False, idempotent=False))
def propose_transactions(
    content: str = "",
    account: str = "",
    currency: str = "",
    file_format: str = "auto",
    content_encoding: str = "auto",
    amount_shape: str = "auto",
    columns: str = "",
    date_format: str = "",
    delimiter: str = "",
    decimal_separator: str = "",
    sign: str = "auto",
    opening_balance: str = "",
    counter_account: str = "",
    statement_id: str = "",
    filename: str = "",
) -> str:
    """Parse a bank statement (CSV or OFX/QFX) into a PROPOSAL. Writes nothing.

    Give `statement_id` when you were given one (`stm_…`): the statement is
    already staged beside the book, so do NOT ask for or copy the file. Otherwise
    give `content` — the statement's bytes, base64-encoded, or its text pasted
    straight in. NOT a path.

    `account` is required: the book account the statement belongs to, e.g.
    'Assets:Checking' or 'Liabilities:Visa'. Every row is one side of it and
    the file never says which.

    Returns a SUMMARY, not the rows: `proposal_id`, `counts`, `date_range`,
    `totals`, the column `mapping` (read it — it says what was assumed and on
    what evidence, including whether the running balance confirmed the sign),
    what was `flagged` and why, and `payees` — each distinct payee once, with
    how many rows and the net amount. A payee is identified by the
    counterparty's IBAN when the statement has one (common in IBAN countries,
    e.g. a German Girokonto export), so different spellings of one
    counterparty are one payee; `names` lists how it was spelled. Without an IBAN, a payee is its normalised name.

    WHAT TO DO NEXT
    - Tell the user the counts, the date range and anything flagged.
    - If the mapping is wrong, call again with the matching override rather
      than fixing numbers yourself: amount_shape ('signed' | 'debit_credit' |
      'balance'), columns (JSON like {"date": "Posted Date", "amount":
      "Amount"}), date_format (strptime), delimiter, decimal_separator,
      sign ('normal' | 'inverted'), opening_balance, currency.
    - Categorise by PAYEE and call `commit_proposal`. Never re-type the rows
      into `add_transactions`.
    """
    book = _require(_book())
    options = dict(
        currency=currency, file_format=file_format, amount_shape=amount_shape,
        columns=columns, date_format=date_format, delimiter=delimiter,
        decimal_separator=decimal_separator, sign=sign,
        opening_balance=opening_balance, counter_account=counter_account,
    )
    if not statement_id:
        statement_id = proposals.stage_statement(
            book.root, proposals.content_to_bytes(content, content_encoding), filename,
        )["statement_id"]
    summary = proposals.propose(book.root, book.read_all(), statement_id, account, **options)
    # Compact on purpose: this string is re-sent on every later turn of the
    # session, so indentation is a per-message cost with no reader.
    return json.dumps(summary, separators=(",", ":"), ensure_ascii=False)



@mcp.tool(**_tool("Write a proposed import", 'write', destructive=False, idempotent=True))
def commit_proposal(proposal_id: str, overrides: str = "", open_new_accounts: bool = False) -> str:
    """Write a proposal from `propose_transactions` into the book, as ONE commit.

    `overrides` is a JSON object with the accounts you chose:
      {"payees": {"DE89370400440532013000": "Expenses:Groceries",
                  "SALARY ACME LTD": "Income:Salary"},
       "rows":   {"cb1:0123abcd4567ef89": "Expenses:Travel"}}
    Payee keys are the `payee` values the proposal listed: an IBAN when the
    statement has one, otherwise a name. One IBAN with several `names` (a
    payment provider such as PayPal) usually needs per-row accounts. `rows` overrides a
    single row by its import_id, and "skip" leaves that row (or that payee) out.
    Anything you do not name stays on Expenses:Unclassified /
    Income:Unclassified — say so to the user rather than guessing.

    The transactions are written from the parsed rows: you choose accounts, you
    never type amounts. bean-check validates and git commits, as with every
    write; a rejected commit changes nothing. Rows already in the book are
    skipped, so calling this twice cannot double-book.

    If an account is not open yet the commit is refused and names it. Confirm
    the names with the user, then call again with open_new_accounts=true to
    open them in the same commit.

    Only commit after the user has agreed.
    """
    # Raises on refusal, unlike `add_transactions`' catch-and-return, on
    # purpose: MCP reports a raise as `isError: true`, so a caller branching on
    # the error flag cannot read a refused commit as a written one.
    book = _require(_book())
    result = proposals.commit(book, proposal_id, overrides, open_new_accounts)
    return json.dumps(result, indent=1)


# ---------------------------------------------------------------- reports ----
@mcp.tool(**_tool("Make a report", 'read', destructive=False, idempotent=True))
def generate_report(format: str = "html", as_of: str = "", period_start: str = "") -> str:
    """Generate a financial report from the book.

    format: 'html' (the paper report page, self-contained) or 'xlsx' (Excel
    workbook with Balance sheet, Income statement, Transactions and To check
    sheets).
    as_of: balance-sheet date, YYYY-MM-DD (default today).
    period_start: first day of the income statement, YYYY-MM-DD (default
    1 January of the as_of year — the income statement is year to date).
    Returns the path to the written file (under the book's `reports/` folder).
    """
    ledger = _require(_book())
    out_dir = ledger.root / "reports"
    out_dir.mkdir(exist_ok=True)
    stamp = (as_of or "latest").replace(":", "-")
    fmt = format.lower()
    if fmt in ("html", "htm"):
        path = reporting.generate_html(ledger, str(out_dir / f"report-{stamp}.html"),
                                       as_of or None, period_start or None)
    elif fmt in ("xlsx", "excel"):
        path = reporting.generate_xlsx(ledger, str(out_dir / f"report-{stamp}.xlsx"),
                                       as_of or None, period_start or None)
    else:
        return f"Unknown format '{format}'. Use 'html' or 'xlsx'."
    return f"Report written to {path}"


# --------------------------------------------------------------- receipts ----
#
# Two tools, not one. `stage_receipt` moves bytes and returns a REFERENCE;
# `propose_receipt_transaction` turns a reading of those bytes into a proposal.
# The reading itself (OCR) is the model's own vision: Claude Code reads the
# file, and nothing here looks at the image.
#
# ⛔ NEITHER TOOL WRITES. `add_transactions` remains the only write path, so the
# bean-check gate and the git commit stay exactly where they are.


@mcp.tool(**_tool("Store a receipt", 'write', destructive=False, idempotent=True))
def stage_receipt(
    file_path: str = "", content_base64: str = "", declared_name: str = ""
) -> str:
    """Store a receipt photo or PDF as evidence and return a reference to it.

    Call this FIRST, before proposing anything. A receipt is the source
    document for the entry it becomes, and an entry whose evidence was thrown
    away is a promise we cannot keep two years from now.

    Give it EITHER `file_path` (a photo or PDF on this machine) OR
    `content_base64` (the raw bytes, base64-encoded). `declared_name` is recorded in the
    reply for your convenience and is used for NOTHING else — never for the
    file type, never for where the bytes are stored.

    Returns JSON with the bucket key and the content hash. Pass both to
    `propose_receipt_transaction`. Then read the image yourself: this tool does
    not look at it.
    """
    try:
        data = receipts.read_input(file_path, content_base64)
    except receipts.ReceiptInputError as e:
        return json.dumps({"error": str(e)}, indent=2)

    media = receipts.sniff_content_type(data)
    if media not in receipts.STORABLE:
        return json.dumps({
            "error": (
                f"That does not look like a receipt image or PDF "
                f"({media or 'unrecognised file type'}). Storable types are "
                f"{', '.join(sorted(receipts.STORABLE))}. The type is read "
                f"from the bytes, not the filename."
            )
        }, indent=2)

    book = _require(_book())
    try:
        stored = receipts.store(book, data, media)
    except LedgerError as e:
        return json.dumps({"error": f"The receipt was NOT stored: {e}"}, indent=2)

    return json.dumps({
        "receipt": stored,
        "declared_name": declared_name,
        "readable_by_model": media != "application/pdf",
        "next_step": receipts.next_step_for(media),
    }, indent=2)


@mcp.tool(**_tool("Propose an entry from a receipt", 'read', destructive=False, idempotent=True))
def propose_receipt_transaction(
    receipt_sha256: str,
    receipt_key: str = "",
    date: str = "",
    date_confidence: float = 0.0,
    merchant: str = "",
    merchant_confidence: float = 0.0,
    total: str = "",
    total_confidence: float = 0.0,
    tax: str = "",
    tax_confidence: float = 0.0,
    currency: str = "",
    currency_confidence: float = 0.0,
    line_items_json: str = "",
    extracted_text: str = "",
    expense_account: str = "",
    paid_from_account: str = "",
    operating_currency: str = "",
    exchange_rate: str = "",
    confidence_floor: float = 0.6,
) -> str:
    """Turn what you read off a staged receipt into a PROPOSED transaction.

    You supply the reading and, for every field, how sure you are of it on a
    0.0-1.0 scale. Be honest about the confidences — they are the whole
    mechanism. **Anything below the floor comes back flagged rather than as a
    value**, and the floor can be raised by argument but never lowered, so a
    low confidence is not a suggestion.

    `expense_account` and `paid_from_account` are yours to choose:
    categorisation is the part of this a model is genuinely good at. The book's
    `open` directives are read here so that two failures which would otherwise
    land AT THE WRITE — an account that was never opened, and an account pinned
    to a currency this receipt is not in — arrive now, as a sentence, instead of
    as a bean-check dump after the user has already approved the entry.

    `exchange_rate` is how much ONE unit of the receipt's currency cost in the
    paying account's currency. Take it off the card statement; no rate is ever
    invented here.

    Returns JSON. It NEVER writes. Show the person the merchant, the date and
    the total, ask about anything flagged, and only then pass `beancount` to
    `add_transactions` unchanged.
    """
    book = _require(_book())
    try:
        items = json.loads(line_items_json) if line_items_json.strip() else []
        if not isinstance(items, list):
            raise ValueError("line_items_json must be a JSON array")
    except (ValueError, TypeError) as e:
        return json.dumps({"error": f"line_items_json is not usable: {e}"}, indent=2)

    # Read the chart of accounts so the two write-time failures become
    # proposal-time ones. A failure to read it is NOT fatal — the proposal is
    # still worth having — but it is reported, because "not checked" and
    # "checked and fine" must never look the same.
    try:
        constraints = receipts.open_currency_constraints(book.read_all())
    except LedgerError:
        constraints = None

    proposal = receipts.build_proposal(
        receipt={"key": receipt_key, "sha256": receipt_sha256},
        date=date, date_confidence=date_confidence,
        merchant=merchant, merchant_confidence=merchant_confidence,
        total=total, total_confidence=total_confidence,
        tax=tax, tax_confidence=tax_confidence,
        currency=currency, currency_confidence=currency_confidence,
        line_items=items,
        extracted_text=extracted_text,
        expense_account=expense_account,
        paid_from_account=paid_from_account,
        operating_currency=operating_currency,
        exchange_rate=exchange_rate,
        account_currencies=constraints,
        confidence_floor=confidence_floor,
    )
    return json.dumps(proposal, indent=2)


# ---------------------------------------------------------------- history ----
@mcp.tool(**_tool("Change history", 'read', destructive=False, idempotent=True))
def history(limit: int = 20) -> str:
    """Show the book's git history (each AI or human change is one commit)."""
    hist = _require(_book()).history(limit)
    return "\n".join(f"{h['commit']} {h['date']} {h['message']}" for h in hist) \
        or "(no history)"


@mcp.tool(**_tool("Undo a change", 'write', destructive=True, idempotent=False))
def revert(commit: str) -> str:
    """Revert a specific commit (undo a change), creating a new commit."""
    res = _require(_book()).revert(commit)
    return f"Reverted {commit} → {res.commit}"


# ------------------------------------------------- how to keep the books ----
#
# The portable instruction pack (instructions.py). Offered as a PROMPT, which
# clients show as a selectable starting message, and as a RESOURCE, which
# clients can attach as context — because different assistants honour one and
# not the other. Same text both ways.
@mcp.prompt(
    name=instructions.PROMPT_NAME,
    title="Bookkeeping rules",
    description=instructions.PROMPT_DESCRIPTION,
)
def bookkeeping() -> str:
    return instructions.BOOKKEEPING


@mcp.resource(
    instructions.RESOURCE_URI,
    name="bookkeeping",
    title="Bookkeeping rules",
    description=instructions.PROMPT_DESCRIPTION,
    mime_type="text/markdown",
)
def bookkeeping_resource() -> str:
    return instructions.BOOKKEEPING


def _put_scripts_on_path() -> None:
    """Make this interpreter's console scripts (bean-check) findable.

    They live in the environment's `bin/` (POSIX) or `Scripts\\` (Windows),
    which is not on PATH unless the environment was activated.
    """
    scripts = sysconfig.get_path("scripts")
    if scripts:
        os.environ["PATH"] = scripts + os.pathsep + os.environ.get("PATH", "")


def main() -> None:
    _put_scripts_on_path()
    mcp.run()


if __name__ == "__main__":
    main()
