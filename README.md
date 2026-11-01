# Beancount plugin for Claude Code

This plugin makes Claude Code a bookkeeper. The ledger stays on your computer.

The plugin has three parts:

- An **MCP server**. It keeps a [Beancount](https://beancount.github.io/) ledger (the "book") in
  a local directory. The book is a git repository. Before each change, the server checks the
  ledger with `bean-check`. The server records only a correct change, and it makes a git commit
  for each change. Thus, you can undo each change.
- **Slash commands**. They create the book, import statements and receipts, and make reports.
- A **skill** (`beancount-accounting`). It gives Claude the rules for correct double-entry
  transactions.

The plugin does not send data to a server. It does not use an account or an API key. The book is
a directory of text files. You own it.

## Requirements

| Tool | Function |
|---|---|
| [Claude Code](https://docs.claude.com/en/docs/claude-code) | Runs the plugin. |
| [uv](https://docs.astral.sh/uv/) | Starts the MCP server. On the first start, uv downloads Python 3.11+ and the dependencies. |
| [git](https://git-scm.com/) | Keeps the history of the book. |

### Install uv

On macOS or Linux, use this command:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows, use this command in PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

You can also use `brew install uv`, `winget install --id astral-sh.uv -e` or `pipx install uv`.

### Install git

Usually, git is already on your computer. On Windows, Claude Code usually needs Git for Windows.

| Operating system | Command |
|---|---|
| Windows | `winget install --id Git.Git -e` |
| macOS | `xcode-select --install` (or `brew install git`) |
| Debian / Ubuntu | `sudo apt install git` |

If git is not available, the first change to the book stops. The error message shows the install
command for your operating system. The book does not change.

## Install the plugin

Type each command separately in Claude Code. Do not paste the commands together. Claude Code
reads a paste of many lines as one slash command, and the install fails.

1. Add the marketplace:

   ```text
   /plugin marketplace add https://github.com/b0r1sp/beancount-plugin.git
   ```

2. Install the plugin:

   ```text
   /plugin install beancount@beancount
   ```

3. Load the plugin into the session:

   ```text
   /reload-plugins
   ```

Use the full HTTPS URL. Do not use the short form `b0r1sp/beancount-plugin`. The short form
uses SSH, and SSH needs a key.

To install from a local clone, use `/plugin marketplace add ./beancount-plugin`. Then do steps 2
and 3.

On the first start, uv makes a virtual environment in the plugin directory. This takes
approximately 30 seconds. Claude Code asks you to approve the MCP server. Approve it. If the
tools are not available after this, type `/reload-plugins` again.

### If the server does not start

1. Type `/mcp`. Claude Code shows the error from the server.
2. Make sure that uv is on the `PATH`. If you installed uv after you started Claude Code,
   restart Claude Code.
3. Make sure that the computer has a network connection for the first start.

### Create the book

Type this command:

```text
/beancount:init
```

## Commands

| Command | Function |
|---|---|
| `/beancount:init [name] [currency]` | Creates the local book and a starter chart of accounts. |
| `/beancount:ingest <file or text>` | Imports a bank or card statement (CSV, OFX/QFX, PDF), a receipt photo or a text description. The result is a set of correct transactions. |
| `/beancount:report [html\|excel\|both] [as-of] [period-start]` | Makes an HTML report, an Excel workbook or the two. The report has a balance sheet, an income statement and the transactions. |
| `/beancount:status [question]` | Shows the net worth, the balances and the recent changes. It can also answer a question about your money. |
| `/beancount:assess [focus]` | Examines the book. It shows the run rate, the categories, the changes, unusual entries and gaps in the data. |
| `/beancount:undo [commit]` | Reverts a change from the git history. |

## Location of your data

| Data | Location |
|---|---|
| The book (a git repository of `.beancount` files) | `~/.beancount/main`. On Windows: `C:\Users\<you>\.beancount\main` |
| Reports | `<book>/reports/`. Git ignores this directory. |
| Statements and import proposals before the import | `<book>/.git/beancount-imports/`. Git does not commit these files. |
| Receipt photos and PDF files | `~/.beancount/receipts/<sha256>`. These files are outside the book. Thus, they do not make the git history large. |

### Use a different directory

Set the variable `BEANCOUNT_BOOK` to a local directory. Do this **before** you start Claude Code.
You can also set the variable in the `env` block of the plugin file `.mcp.json`.

On macOS or Linux:

```bash
export BEANCOUNT_BOOK=~/finance/books
```

On Windows (the setting applies to new terminals):

```powershell
setx BEANCOUNT_BOOK "D:\finance\books"
```

To see the directory that the plugin uses, ask Claude "where is my book?". Claude uses the
`book_location` tool.

The book is a git repository of text files. To make a backup, push the repository to a remote.

## Examine the book in Fava

[Fava](https://beancount.github.io/fava/) is a web interface for Beancount. Fava needs only uv.

On macOS or Linux:

```bash
uvx fava ~/.beancount/main/main.beancount
```

On Windows:

```powershell
uvx fava $env:USERPROFILE\.beancount\main\main.beancount
```

Then open <http://localhost:5000> in a browser. Fava shows the entries that Claude flags with `!`
as pending.

If you change the ledger in Fava, commit the change yourself. In the book directory, type
`git commit -am "<message>"`. The plugin adds to git only the files that it writes.

## MCP tools

The server has these tools:

`book_location`, `create_book`, `book_status`, `list_accounts`, `get_ledger`, `open_accounts`,
`add_transactions`, `add_directives`, `propose_transactions`, `commit_proposal`, `run_query`,
`balances`, `assess_book`, `generate_report`, `history`, `revert`, `stage_receipt`,
`propose_receipt_transaction`.

The server also has a `bookkeeping` prompt and a `bookkeeping` resource. They contain the
bookkeeping rules for each MCP client.

### Statement import

1. `propose_transactions` reads a CSV, OFX or QFX statement for one account. It does not change
   the book. It returns a summary with a `proposal_id`. The summary shows the column mapping,
   the counts, the flagged rows and each payee one time.
2. You select an account for each payee.
3. `commit_proposal` writes the proposal with these accounts. The server checks the result and
   makes one commit.

The rows do not go through the model. Thus, the model does not limit the size of a statement.

### Receipt import

1. `stage_receipt` keeps the photo or the PDF file as evidence. It returns a reference.
2. Claude reads the receipt.
3. `propose_receipt_transaction` makes a proposed entry from the data that Claude read. If a
   value is not certain, the tool flags the value. It does not supply a value.

The tools do not do OCR. Claude reads the receipt.

### Tools that change the book

These tools change the book: `commit_proposal`, `add_transactions`, `open_accounts`,
`add_directives` and `revert`.

These tools do not change the book: `propose_transactions`, `stage_receipt` and
`propose_receipt_transaction`.

## Development

Use these commands in the `plugin` directory.

To start the stdio server:

```bash
uv run beancount-mcp
```

To start the server with a test book:

```bash
BEANCOUNT_BOOK=/tmp/testbook uv run beancount-mcp
```

Structure of the repository:

```
.claude-plugin/marketplace.json   marketplace entry
plugin/.claude-plugin/plugin.json plugin manifest
plugin/.mcp.json                  starts the server with `uv run`
plugin/pyproject.toml             Python package (beancount-mcp)
plugin/mcp/beancount_mcp/         the MCP server
plugin/commands/                  slash commands
plugin/skills/                    the beancount-accounting skill
```

## License

[MIT](LICENSE)
