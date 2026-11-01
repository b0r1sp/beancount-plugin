# Beancount plugin

Turn your coding assistant into your bookkeeper, on a ledger that lives entirely on your
machine. The plugin bundles:

- an **MCP server** that manages a local, git-backed [Beancount](https://beancount.github.io/)
  ledger (your "book") — every write is validated with `bean-check` and committed to git, so the
  ledger is never left broken and every change can be undone;
- **slash commands** to set it up, ingest statements and receipts, and produce reports;
- a **skill** (`beancount-accounting`) that teaches the assistant correct double-entry so transactions
  land valid the first time.

Nothing leaves your computer: there is no account, no server, no API key. The book is a plain-text
directory you own.

## Requirements

| Tool | Why | Install |
|---|---|---|
| An assistant with plugin support (`/plugin`) | runs the plugin | — |
| [uv](https://docs.astral.sh/uv/) | starts the MCP server; downloads Python 3.11+ and the dependencies by itself on first run | see below |
| [git](https://git-scm.com/) | every book is a git repository | see below |

**uv** — one command per OS:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

(or `brew install uv`, `winget install --id astral-sh.uv -e`, `pipx install uv`).

**git** — usually already installed (on Windows, the assistant generally needs Git for Windows anyway):

| OS | Command |
|---|---|
| Windows | `winget install --id Git.Git -e` |
| macOS | `xcode-select --install` (or `brew install git`) |
| Debian / Ubuntu | `sudo apt install git` |

If git is missing, the first write fails with a message naming the install command for your OS;
nothing is written.

## Install

Run these one at a time — the assistant reads a multi-line paste as a *single* slash command.

```text
/plugin marketplace add https://github.com/b0r1sp/beancount-plugin.git
```

```text
/plugin install beancount@beancount
```

Then reload, so the running session picks up the plugin's commands and MCP tools:

```text
/reload-plugins
```

Use the full HTTPS URL rather than the `b0r1sp/beancount-plugin` shorthand — the shorthand
resolves to `git@github.com`, which needs an SSH key. From a local clone:
`/plugin marketplace add ./beancount-plugin`, then the same install.

On first use, `uv` creates a virtualenv inside the plugin (beancount, beanquery, openpyxl, mcp),
downloading a suitable Python if you don't have one. That takes ~30s once. You'll be asked to
approve the MCP server; run `/reload-plugins` again if the tools still don't appear.

If the server fails to connect, run `/mcp` — the assistant shows the error the server printed. The
usual causes are `uv` not being on the `PATH` the assistant was started with (restart it
after installing uv) or no network on the very first run.

Then create your book:

```text
/beancount:init
```

## Commands

| Command | What it does |
|---|---|
| `/beancount:init [name] [currency]` | Create the local book plus a starter chart of accounts. |
| `/beancount:ingest <file or text>` | Import a bank/card statement (CSV, OFX/QFX, PDF), a receipt photo — or plain text — into validated transactions. |
| `/beancount:report [html\|excel\|both] [as-of] [period-start]` | Generate a styled HTML report and/or an Excel workbook (balance sheet, income statement, transactions). |
| `/beancount:status [question]` | Net worth, balances, recent changes — or answer a specific money question. |
| `/beancount:assess [focus]` | Review the book: run rate, categories, changes, unusual entries, gaps in the data. |
| `/beancount:undo [commit]` | Revert a change from git history. |

## Where your data lives

| What | Where |
|---|---|
| The book (a git repo of `.beancount` files) | `~/.beancount/main` — on Windows `C:\Users\<you>\.beancount\main` |
| Reports | `<book>/reports/` (git-ignored) |
| Staged statements and import proposals | `<book>/.git/beancount-imports/` (never committed) |
| Receipt photos / PDFs | `~/.beancount/receipts/<sha256>` — kept outside the book so binaries don't bloat its git history |

To keep the book somewhere else, set `BEANCOUNT_BOOK` to any local directory **before the assistant
starts** (or put it in the `env` block of the plugin's `.mcp.json`):

```bash
export BEANCOUNT_BOOK=~/finance/books      # macOS / Linux
```

```powershell
setx BEANCOUNT_BOOK "D:\finance\books"     # Windows (new terminals)
```

Ask "where is my book?" (the `book_location` tool) to see which directory is in use and
why. Because the book is plain text under git, you can back it up by pushing it to any remote you
like.

## Browsing the book in Fava

[Fava](https://beancount.github.io/fava/) is a web UI for Beancount ledgers. With uv installed
there is nothing else to set up:

```bash
uvx fava ~/.beancount/main/main.beancount
```

```powershell
uvx fava $env:USERPROFILE\.beancount\main\main.beancount
```

Then open <http://localhost:5000>. Entries flagged with `!` show up there as pending. If you
edit the ledger in Fava, commit those changes yourself (`git commit -am "..."` in the book
directory) — the plugin only stages the files it writes.

## MCP tools (for reference)

`book_location`, `create_book`, `book_status`, `list_accounts`, `get_ledger`, `open_accounts`,
`add_transactions`, `add_directives`, `propose_transactions`, `commit_proposal`, `run_query`,
`balances`, `assess_book`, `generate_report`, `history`, `revert`, `stage_receipt`,
`propose_receipt_transaction`. A `bookkeeping` prompt and resource carry the bookkeeping rules
for any MCP client.

`propose_transactions` is the statement importer: give it a CSV or OFX/QFX and the account it
belongs to, and it parses it and returns a summary — the column mapping it detected, counts, what
it flagged and each payee once — under a `proposal_id`. `commit_proposal` then writes that
proposal with the accounts you chose per payee, validated and committed like every write. The
rows never pass through the model, so a statement's size is not limited by what a model can copy.

`stage_receipt` and `propose_receipt_transaction` are the receipt path. The first keeps the photo
or PDF as evidence and returns a reference; the second turns what the assistant read off it into a
proposed entry with anything uncertain flagged rather than filled in. Neither does OCR: the
reading is the model's own, on purpose.

None of `propose_transactions`, `stage_receipt` or `propose_receipt_transaction` writes;
`commit_proposal`, `add_transactions`, `open_accounts`, `add_directives` and `revert` do.

## Development

```bash
cd plugin
uv run beancount-mcp                                  # runs the stdio server
BEANCOUNT_BOOK=/tmp/testbook uv run beancount-mcp     # against a scratch book
```

Layout:

```
plugin/.mcp.json                  starts the server with `uv run`
plugin/pyproject.toml             Python package (beancount-mcp)
plugin/mcp/beancount_mcp/         the MCP server
plugin/commands/                  slash commands
plugin/skills/                    the beancount-accounting skill
```

## License

[MIT](LICENSE)
