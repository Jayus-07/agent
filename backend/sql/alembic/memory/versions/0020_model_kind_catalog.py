"""0020 — 为模型目录增加用途类型，区分文本、向量与重排模型。"""
from alembic import op


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE llm_models
            ADD COLUMN IF NOT EXISTS model_kind TEXT NOT NULL DEFAULT 'chat'
        """
    )
    op.execute(
        """
        UPDATE llm_models
        SET model_kind = 'chat'
        WHERE model_kind IS NULL
           OR model_kind NOT IN ('chat', 'embedding', 'rerank')
        """
    )
    op.execute(
        """
        ALTER TABLE llm_models
            DROP CONSTRAINT IF EXISTS llm_models_model_kind_check
        """
    )
    op.execute(
        """
        ALTER TABLE llm_models
            ADD CONSTRAINT llm_models_model_kind_check
            CHECK (model_kind IN ('chat', 'embedding', 'rerank'))
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_llm_models_provider_kind
        ON llm_models(provider_id, model_kind, enabled)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_llm_models_provider_kind")
    op.execute(
        "ALTER TABLE llm_models DROP CONSTRAINT IF EXISTS llm_models_model_kind_check"
    )
    op.execute("ALTER TABLE llm_models DROP COLUMN IF EXISTS model_kind")
