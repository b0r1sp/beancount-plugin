---
description: Initialise a local Beancount book (a git-backed ledger in a directory on this machine) plus a starter chart of accounts.
argument-hint: "[book name] [currency]"
---

You are setting up the user's local Beancount book. Arguments: `$ARGUMENTS`
(first token = book name, optional; second = 3-letter currency, optional, default USD).

Do this in order:

1. **Find out where the book lives.** Call `book_location`.
   - If the tools are unavailable entirely (no `mcp__beancount__*`), the MCP server did not
     start. Tell the user to run `/mcp` to see the error the server printed, and that
     `/reload-plugins` retries it. The usual causes are `uv` not being installed, or the first
     launch still downloading Python and the dependencies (~30s). Stop here; nothing below can
     work.
   - If the book already exists, report its location and go to step 4.

2. **Choose the location.** Ask the user where to keep the book. Offer the location from step 1
   (by default `~/.beancount/main`), and say that any local directory works — for example a
   folder in their documents or a folder they sync. Ask for the full path.

3. **Create the book.** Call `create_book` with `name` (from `$1`, else ask, else "My Books") and
   `currency` (`$2` uppercased, else "USD"). If the user chose a different directory, also pass
   `path` with its full path: `create_book` then saves it as the book location, and every other
   tool uses it. If it refuses because the directory already has some of the book's files, relay
   that and ask for another directory. If it fails with "`git` not found", relay the install
   command it gives and stop.

4. **Seed the opening structure.** Offer to set up a starter chart of accounts. If the user
   agrees (or says something like "set up my money situation"), use `open_accounts` to open
   sensible accounts — typically `Assets:Checking`, `Assets:Savings`,
   `Liabilities:CreditCard`, `Income:*`, `Expenses:*` — following the double-entry rules in the
   beancount-accounting skill. Skip this if the book already has accounts.

5. **Confirm against the book, not your own memory.** Call `book_status` and show the location,
   currency and account count it returns. Then point at what is next:
   `/beancount:ingest <file>` to import statements, `/beancount:report` for reports, and Fava
   (`uvx fava <book>/main.beancount`) for a browser view of the ledger.

Keep it brief and friendly. Never invent balances — only record what the user tells you.
