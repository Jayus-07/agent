"""0021 — 将已有专项模型纳入统一模型目录。"""
from alembic import op


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """回填历史 embedding/rerank 绑定，使其可在供应商页和角色下拉中使用。"""
    op.execute(
        """
        INSERT INTO llm_models
            (name, provider_id, display_name, model_kind, source, created_by, updated_at)
        SELECT model_name, provider_id, model_name, role, 'user', 'system', now()
        FROM llm_specialized_model_bindings
        WHERE enabled = true
        ON CONFLICT (name) DO UPDATE SET
            model_kind = CASE
                WHEN llm_models.provider_id = EXCLUDED.provider_id
                THEN EXCLUDED.model_kind
                ELSE llm_models.model_kind
            END,
            updated_at = CASE
                WHEN llm_models.provider_id = EXCLUDED.provider_id
                THEN now()
                ELSE llm_models.updated_at
            END
        """
    )


def downgrade() -> None:
    """不删除目录记录，避免回滚迁移误删用户登记的模型。"""
    pass
