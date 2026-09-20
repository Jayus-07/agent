"""0018 — 模型角色绑定、配置历史与供应商探测状态。

0017 只提供供应商 / 凭据 / 模型注册表；本迁移补齐管理端上线所需的
角色覆盖、不可逆密钥审计和探测结果持久化。历史表只保存展示值与密钥指纹，
绝不保存明文或 Fernet 密文。
"""
from alembic import op


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO llm_providers
            (id, display_name, driver, base_url, billing, is_builtin, enabled)
        VALUES
            ('ollama', 'Ollama', 'ollama', '', 'local', true, true),
            ('deepseek', 'DeepSeek', 'openai', '', 'metered', true, true),
            ('minimax', 'MiniMax', 'anthropic', '', 'metered', true, true),
            ('qwen', '通义千问', 'openai', '', 'metered', true, true),
            ('qwen_tp', '通义千问 Token Plan', 'openai', '', 'subscription', true, true),
            ('vllm', '自托管 vLLM', 'openai', '', 'local', true, true),
            ('siliconflow', '硅基流动', 'openai', '', 'metered', true, true)
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        ALTER TABLE llm_providers
            ADD COLUMN IF NOT EXISTS last_probe_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS last_probe_ok BOOLEAN,
            ADD COLUMN IF NOT EXISTS last_probe_worst_grade TEXT
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_model_role_bindings (
            role TEXT PRIMARY KEY,
            model_name TEXT NOT NULL,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_config_history (
            id BIGSERIAL PRIMARY KEY,
            object_type TEXT NOT NULL
                CHECK (object_type IN (
                    'role', 'provider', 'provider_credential',
                    'provider_network_scope'
                )),
            object_key TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            secret_fingerprint TEXT,
            operator TEXT NOT NULL DEFAULT '',
            rollbackable BOOLEAN NOT NULL DEFAULT true,
            changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_llm_config_history_changed_at
        ON llm_config_history(changed_at DESC, id DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_llm_config_history_object
        ON llm_config_history(object_type, object_key, changed_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_llm_config_history_object")
    op.execute("DROP INDEX IF EXISTS idx_llm_config_history_changed_at")
    op.execute("DROP TABLE IF EXISTS llm_config_history")
    op.execute("DROP TABLE IF EXISTS llm_model_role_bindings")
    op.execute(
        """
        ALTER TABLE llm_providers
            DROP COLUMN IF EXISTS last_probe_worst_grade,
            DROP COLUMN IF EXISTS last_probe_ok,
            DROP COLUMN IF EXISTS last_probe_at
        """
    )
