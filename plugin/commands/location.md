---
description: Show or change the local directory of your Beancount book.
argument-hint: "[full path to a directory | default]"
---

Show or change where the user's book lives. Arguments: `$ARGUMENTS`

- **No argument:** call `book_location` and report the directory, what chose it, and whether a
  book exists there.
- **A path:** call `set_book_location(path="$ARGUMENTS")`. It needs a FULL path; if the user gave
  a relative one, ask for the full path instead of guessing. Relay the result. If it says there
  is no book there yet, offer to create one with `create_book` (or `/beancount:init`).
- **`default`:** call `set_book_location(path="")` to go back to `~/.beancount/main`.

Changing the location does not move or copy a book. To move an existing book, tell the user to
move the whole directory (including its hidden `.git` folder) first, then set the location to
the new place.

If the result warns that `BEANCOUNT_BOOK` overrides the setting, relay it: that variable wins
until it is removed and the app is restarted.
