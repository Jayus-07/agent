"""Deployment contract for the one-shot database migration service."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_backend_rebuild_includes_migration_runner() -> None:
    """The service set built and started by devctl must include db-migrate."""
    script = (ROOT / "dev-svc.bat").read_text(encoding="ascii")
    marker = 'set "BACKEND_SERVICES='
    service_line = next(
        line for line in script.splitlines() if line.startswith(marker)
    )
    services = service_line.removeprefix(marker).removesuffix('"').split()

    assert "db-migrate" in services


def test_rebuild_confirmation_flags_are_not_treated_as_targets() -> None:
    """Confirmation flags must leave the default rebuild target intact."""
    script = (ROOT / "dev-svc.bat").read_text(encoding="ascii")
    parser = script.split(":: ---- parse args:", 1)[1].split(
        'if "!ACTION!"=="" set "ACTION=status"', 1
    )[0]

    assert 'if /i "!TOK!"=="-y" (' in parser
    assert 'if /i "!TOK!"=="--yes" (' in parser
    assert 'if "!IS_CONFIRM_FLAG!"=="0" if not "!TOK:~0,1!"=="/" (' in parser
    assert 'if "!TARGET!"=="" set "TARGET=all"' in script
