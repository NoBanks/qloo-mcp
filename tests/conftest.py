"""Shared test setup: keep the shipped demo fixtures out of unrelated tests."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_fixtures(monkeypatch, tmp_path):
    """Point the demo cache at an empty dir and clear the demo flags.

    Without this, a mocked 401 in an unrelated test would be answered from the
    committed sample fixtures instead of surfacing as an error.
    """
    monkeypatch.setenv("QLOO_FIXTURES_DIR", str(tmp_path / "fixtures"))
    monkeypatch.delenv("QLOO_DEMO", raising=False)
    monkeypatch.delenv("QLOO_RECORD", raising=False)
