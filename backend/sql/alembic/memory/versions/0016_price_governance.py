"""0016 — 模型价格版本、分离审核与灰度生效状态。"""
from alembic import op


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS model_price_versions (
            version TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            imported_by TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'reviewed_1', 'scheduled',
                                  'canary', 'active', 'rejected', 'expired')),
            effective_from TIMESTAMPTZ NOT NULL,
            reviewer_1 TEXT,
            reviewer_2 TEXT,
            canary_started_at TIMESTAMPTZ,
            canary_completed_at TIMESTAMPTZ,
            coverage_ratio NUMERIC(6, 5) NOT NULL DEFAULT 0,
            missing_price_count INTEGER NOT NULL DEFAULT 0
                CHECK (missing_price_count >= 0),
            price_calculation_error_count INTEGER NOT NULL DEFAULT 0
                CHECK (price_calculation_error_count >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (reviewer_1 IS NULL OR reviewer_1 <> imported_by),
            CHECK (reviewer_2 IS NULL OR reviewer_2 <> imported_by),
            CHECK (reviewer_1 IS NULL OR reviewer_2 IS NULL OR reviewer_1 <> reviewer_2)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS model_price_reviews (
            id BIGSERIAL PRIMARY KEY,
            version TEXT NOT NULL REFERENCES model_price_versions(version),
            reviewer TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
            reason TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (version, reviewer)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_model_price_versions_status
        ON model_price_versions(status, updated_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_price_reviews")
    op.execute("DROP TABLE IF EXISTS model_price_versions")
