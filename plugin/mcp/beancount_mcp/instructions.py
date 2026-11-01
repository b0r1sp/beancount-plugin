"""The portable bookkeeping rules, for any assistant using this server.

ONE TEXT, SERVED THREE WAYS, so no copy can drift:

* ``bookkeeping.md`` beside this file — the source, and plain markdown a person
  can paste into an assistant's custom instructions;
* an MCP prompt and an MCP resource (server.py), for clients that offer them;
* a short summary in ``SERVER_INSTRUCTIONS``, sent at ``initialize``.
"""
from __future__ import annotations

from pathlib import Path

_SOURCE = Path(__file__).with_name("bookkeeping.md")

BOOKKEEPING: str = _SOURCE.read_text(encoding="utf-8")

PROMPT_NAME = "bookkeeping"
RESOURCE_URI = "beancount://instructions/bookkeeping"
PROMPT_DESCRIPTION = (
    "How to keep these books: propose before writing, never multiply by a "
    "rate yourself, ask when unsure. Read it before changing the book."
)

# Sent to every client at `initialize` (the MCP `instructions` field), so it is
# what an assistant sees even when it never opens the prompt or the resource.
# Short on purpose: some clients put it in every turn's context.
SERVER_INSTRUCTIONS = (
    "This server keeps one person's double-entry books (Beancount) in a local "
    "directory. Every write is "
    "checked with bean-check and committed to git. Before any tool that "
    "changes the book, show the person exactly what you will record and wait "
    "for a yes. Never convert currencies or multiply by a rate yourself: "
    "convert inside run_query with convert(), or write the rate exactly as "
    "the source gives it. When unsure which account, ask, or flag the entry "
    "with '!'. Never invent rows. Statements go through propose_transactions "
    "and commit_proposal, not retyped. The full rules are the "
    f"'{PROMPT_NAME}' prompt and the {RESOURCE_URI} resource."
)
