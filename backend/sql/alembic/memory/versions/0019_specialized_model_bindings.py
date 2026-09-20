"""0019 — embedding/rerank 专项模型绑定与探测状态。"""
from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 供应商元数据仍由 llm_providers 统一承载；specialized 只表示该实例
    # 由专项适配器使用，不会被通用聊天工厂当成可调用的 chat driver。
    op.execute(
        """
        ALTER TABLE llm_providers
            DROP CONSTRAINT IF EXISTS llm_providers_driver_check
        """
    )
    op.execute(
        """
        ALTER TABLE llm_providers
            ADD CONSTRAINT llm_providers_driver_check
            CHECK (driver IN ('openai', 'anthropic', 'ollama', 'specialized'))
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_specialized_model_bindings (
            role TEXT PRIMARY KEY
                CHECK (role IN ('embedding', 'rerank')),
            provider_id TEXT NOT NULL
                REFERENCES llm_providers(id) ON DELETE RESTRICT,
            -- 适配器是后端白名单值；不把每个新协议都固化成数据库 CHECK。
            adapter TEXT NOT NULL,
            model_name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            options JSONB NOT NULL DEFAULT '{}'::jsonb,
            enabled BOOLEAN NOT NULL DEFAULT true,
            last_probe_at TIMESTAMPTZ,
            last_probe_ok BOOLEAN,
            last_probe_summary TEXT,
            last_probe_elapsed_ms INTEGER,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_llm_specialized_bindings_provider
        ON llm_specialized_model_bindings(provider_id, enabled)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_llm_specialized_bindings_provider")
    op.execute("DROP TABLE IF EXISTS llm_specialized_model_bindings")
    op.execute(
        """
        ALTER TABLE llm_providers
            DROP CONSTRAINT IF EXISTS llm_providers_driver_check
        """
    )
    op.execute(
        """
        ALTER TABLE llm_providers
            ADD CONSTRAINT llm_providers_driver_check
            CHECK (driver IN ('openai', 'anthropic', 'ollama'))
        """
    )
