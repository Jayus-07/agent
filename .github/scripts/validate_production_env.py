"""在生产部署前检查必须的环境配置，不打印任何密钥内容。"""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

from dotenv import dotenv_values


def validate_production_env(path: Path) -> list[str]:
    """检查预算模式和 Fernet 密钥格式，只返回不含敏感值的错误。"""
    if not path.is_file():
        return ["production environment file is missing"]

    values = dotenv_values(path)
    errors: list[str] = []
    budget_mode = (values.get("LLM_BUDGET_MODE") or "").strip().lower()
    if budget_mode not in {"off", "observe", "enforce"}:
        errors.append(
            "LLM_BUDGET_MODE must be explicitly set to off, observe, or enforce"
        )

    encryption_key = (values.get("SECRETS_ENCRYPTION_KEY") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}=", encryption_key):
        errors.append(
            "SECRETS_ENCRYPTION_KEY must be a valid Fernet key; restore the original key"
        )
    else:
        try:
            decoded = base64.urlsafe_b64decode(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            errors.append("SECRETS_ENCRYPTION_KEY must be a valid Fernet key")
        else:
            if len(decoded) != 32:
                errors.append("SECRETS_ENCRYPTION_KEY must be a valid Fernet key")

    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_production_env.py <env-file>", file=sys.stderr)
        return 2

    errors = validate_production_env(Path(sys.argv[1]))
    if errors:
        for error in errors:
            print(f"[production preflight] {error}", file=sys.stderr)
        return 1

    print("[production preflight] required settings are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
