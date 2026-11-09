"""Shared test setup.

Every test runs with its own HOME, so the book (~/.beancount/main), the saved
location (~/.beancount/config.json) and staged receipts never touch the real
home directory of the person who runs the tests.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Path.home() on Windows
    monkeypatch.delenv("BEANCOUNT_BOOK", raising=False)
    return home


@pytest.fixture
def server():
    from beancount_mcp import server as srv
    return srv


@pytest.fixture
def book(server):
    """An empty EUR book at the default location."""
    server.create_book("Test", "EUR")
    return server
