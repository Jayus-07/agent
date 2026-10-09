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
