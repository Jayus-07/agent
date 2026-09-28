"""baseline fixture 的稳定模块入口。"""

from __future__ import annotations

import sys


def test_import_fixture_delegates_to_canonical_fixture_set(monkeypatch):
    from backend.evaluation import import_fixture
    from backend.scripts import ingest_eval_fixtures

    seen: list[list[str]] = []

    def fake_main():
        seen.append(sys.argv[:])
        return 0

    monkeypatch.setattr(ingest_eval_fixtures, "main", fake_main)
    bootstrapped: list[bool] = []
    monkeypatch.setattr(
        import_fixture,
        "_bootstrap_llm_registry",
        lambda: bootstrapped.append(True),
    )

    assert import_fixture.main(["baseline"]) == 0
    assert bootstrapped == [True]
    assert "--fixture-set" in seen[0]
    assert "baseline" in seen[0]
    assert sys.argv[0] != "--fixture-set"
