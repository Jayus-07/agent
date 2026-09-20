"""0017 — LLM 供应商实例 / 凭据 / 用户自建模型（BYOK）。

设计见 docs/model-config-governance-design.md 附录 B.3。

⚠️ **提交依赖**：本迁移 `down_revision = "0016"`，而 `0014` / `0015` / `0016`
当前属另一会话的在途工作（git 状态为 untracked）。若本文件先于它们提交，
`alembic upgrade head` 会报 `Can't locate revision identified by '0016'` —— **链断**。
故本文件须与 0016 一并（或在 0016 落地之后）提交。

三张表：
  llm_providers              厂商实例（builtin 也留行，便于统一展示 / 停用）
  llm_provider_credentials   与 provider 1:1 的密文凭据
  llm_models                 用户自建模型（builtin 仍留代码层）

两个关键约束：
- `key_cipher` 必须 TEXT：Fernet 密文长度 >180 字符，VARCHAR(128) 装不下。
- 审计只落 `key_fingerprint` / `key_last4`，**任何历史表都不得出现明文或密文**。

执行：`alembic -c alembic.ini -n memory upgrade head`
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_providers (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            -- 协议适配族：代码白名单，用户不可改（配错即不可用，见设计 §3.2）
            driver TEXT NOT NULL
                CHECK (driver IN ('openai', 'anthropic', 'ollama')),
            base_url TEXT NOT NULL DEFAULT '',
            -- public 走 url_guard 全量检查；private 需管理员**显式勾选**才跳过
            -- IP 段检查（不得由「解析出来是私网」自动放行，否则 DNS rebinding 绕过）
            network_scope TEXT NOT NULL DEFAULT 'public'
                CHECK (network_scope IN ('public', 'private')),
            extra_headers JSONB NOT NULL DEFAULT '{}'::jsonb,
            -- 计费口径：metered 按价格表 / subscription 恒 0 但显示「订阅制」/
            -- local 自托管恒 0。三者语义不可合并（见设计 B.3）。
            billing TEXT NOT NULL DEFAULT 'metered'
                CHECK (billing IN ('metered', 'subscription', 'local')),
            is_builtin BOOLEAN NOT NULL DEFAULT false,
            enabled BOOLEAN NOT NULL DEFAULT true,
            created_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_provider_credentials (
            provider_id TEXT PRIMARY KEY
                REFERENCES llm_providers(id) ON DELETE CASCADE,
            -- Fernet 密文（TEXT：VARCHAR(128) 装不下）
            key_cipher TEXT NOT NULL,
            -- 脱敏展示 + 审计用；明文与密文都不得进审计表
            key_fingerprint TEXT NOT NULL DEFAULT '',
            key_last4 TEXT NOT NULL DEFAULT '',
            -- 轮换计数：与缓存实例比对可发现「密钥已换但仍用旧实例」
            key_version INTEGER NOT NULL DEFAULT 1 CHECK (key_version >= 0),
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_models (
            -- 大小写敏感：MiniMax-M3 / Qwen/Qwen3-32B / BAAI/bge-m3 不得被 lower 破坏
            name TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL
                REFERENCES llm_providers(id) ON DELETE CASCADE,
            display_name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
            context_length INTEGER,
            pricing JSONB NOT NULL DEFAULT '{}'::jsonb,
            enabled BOOLEAN NOT NULL DEFAULT true,
            source TEXT NOT NULL DEFAULT 'user'
                CHECK (source IN ('builtin', 'user')),
            created_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_llm_providers_enabled
        ON llm_providers(enabled, is_builtin)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_llm_models_provider
        ON llm_models(provider_id, enabled)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_llm_models_provider")
    op.execute("DROP INDEX IF EXISTS idx_llm_providers_enabled")
    op.execute("DROP TABLE IF EXISTS llm_models")
    op.execute("DROP TABLE IF EXISTS llm_provider_credentials")
    op.execute("DROP TABLE IF EXISTS llm_providers")
