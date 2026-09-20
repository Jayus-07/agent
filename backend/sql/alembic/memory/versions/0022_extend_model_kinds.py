"""0022 — 统一模型目录支持视觉与语音用途。"""
from alembic import op


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
            CHECK (model_kind IN ('chat', 'embedding', 'rerank', 'vision', 'speech'))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE llm_models
        SET model_kind = 'chat'
        WHERE model_kind IN ('vision', 'speech')
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
