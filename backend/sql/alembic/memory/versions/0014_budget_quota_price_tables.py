"""0014 — Q4 用户/租户预算与 Q8 追加式模型价格表。"""
from alembic import op


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS model_price (
            id BIGSERIAL PRIMARY KEY,
            model_name TEXT NOT NULL,
            component TEXT NOT NULL CHECK (component IN ('llm', 'embedding', 'rerank')),
            dimension TEXT NOT NULL CHECK (
                dimension IN ('input', 'output', 'cache_read', 'cache_write',
                              'reasoning', 'tool_call')
            ),
            price_per_unit NUMERIC(18, 6) NOT NULL CHECK (price_per_unit >= 0),
            unit TEXT NOT NULL DEFAULT 'per_1m_tokens'
                CHECK (unit IN ('per_1m_tokens', 'per_call')),
            currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
            price_table_version TEXT NOT NULL,
            source TEXT NOT NULL,
            effective_from TIMESTAMPTZ NOT NULL,
            effective_to TIMESTAMPTZ,
            approval_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (approval_status IN ('pending', 'approved', 'rejected')),
            reviewer_1 TEXT,
            reviewer_2 TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (
                approval_status <> 'approved'
                OR (
                    reviewer_1 IS NOT NULL AND reviewer_1 <> ''
                    AND reviewer_2 IS NOT NULL AND reviewer_2 <> ''
                    AND reviewer_1 <> reviewer_2
                )
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_model_price_effective
        ON model_price(model_name, component, dimension, effective_from DESC)
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_model_price_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'model_price is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS model_price_append_only ON model_price;
        CREATE TRIGGER model_price_append_only
        BEFORE UPDATE OR DELETE ON model_price
        FOR EACH ROW EXECUTE FUNCTION reject_model_price_mutation()
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS budget_policies (
            scope_type TEXT NOT NULL CHECK (
                scope_type IN ('user', 'tenant', 'tenant_default', 'platform')
            ),
            scope_id TEXT NOT NULL,
            daily_limit_usd NUMERIC(18, 6) NOT NULL CHECK (daily_limit_usd > 0),
            monthly_limit_usd NUMERIC(18, 6) NOT NULL CHECK (monthly_limit_usd > 0),
            enforcement TEXT NOT NULL DEFAULT 'hard'
                CHECK (enforcement IN ('hard', 'soft', 'audit')),
            timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
            audit_exempt BOOLEAN NOT NULL DEFAULT false,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (scope_type, scope_id),
            CHECK (
                NOT audit_exempt
                OR (
                    scope_type = 'tenant'
                    AND scope_id LIKE 'test%'
                    AND enforcement = 'audit'
                )
            )
        )
        """
    )
    op.execute(
        """
        INSERT INTO budget_policies
            (scope_type, scope_id, daily_limit_usd, monthly_limit_usd,
             enforcement, timezone, updated_by)
        VALUES
            ('platform', 'default', 20.000000, 300.000000,
             'hard', 'Asia/Shanghai', 'migration-0014'),
            ('tenant_default', 'default', 60.000000, 1000.000000,
             'hard', 'Asia/Shanghai', 'migration-0014'),
            ('tenant', 'platform-default', 20.000000, 300.000000,
             'hard', 'Asia/Shanghai', 'migration-0014'),
            ('tenant', 'internal', 100.000000, 2000.000000,
             'hard', 'Asia/Shanghai', 'migration-0014'),
            ('tenant', 'test', 10.000000, 100.000000,
             'soft', 'Asia/Shanghai', 'migration-0014')
        ON CONFLICT (scope_type, scope_id) DO NOTHING
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS budget_ledger (
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            period_type TEXT NOT NULL CHECK (period_type IN ('day', 'month')),
            period_start TIMESTAMPTZ NOT NULL,
            limit_usd NUMERIC(18, 6) NOT NULL CHECK (limit_usd > 0),
            enforcement TEXT NOT NULL CHECK (enforcement IN ('hard', 'soft', 'audit')),
            used_usd NUMERIC(18, 6) NOT NULL DEFAULT 0 CHECK (used_usd >= 0),
            reserved_usd NUMERIC(18, 6) NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (scope_type, scope_id, period_type, period_start)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS budget_reservations (
            id UUID NOT NULL,
            request_id TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            period_type TEXT NOT NULL CHECK (period_type IN ('day', 'month')),
            period_start TIMESTAMPTZ NOT NULL,
            reserved_usd NUMERIC(18, 6) NOT NULL CHECK (reserved_usd >= 0),
            settled_usd NUMERIC(18, 6) NOT NULL DEFAULT 0 CHECK (settled_usd >= 0),
            status TEXT NOT NULL DEFAULT 'reserved'
                CHECK (status IN ('reserved', 'settled', 'released')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            settled_at TIMESTAMPTZ,
            PRIMARY KEY (id, scope_type, scope_id, period_type)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_budget_reservations_request
        ON budget_reservations(request_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS budget_events (
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            period_type TEXT NOT NULL CHECK (period_type IN ('day', 'month')),
            period_start TIMESTAMPTZ NOT NULL,
            threshold NUMERIC(5, 4) NOT NULL CHECK (threshold IN (0.8000, 1.0000)),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (scope_type, scope_id, period_type, period_start, threshold)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS budget_events")
    op.execute("DROP TABLE IF EXISTS budget_reservations")
    op.execute("DROP TABLE IF EXISTS budget_ledger")
    op.execute("DROP TABLE IF EXISTS budget_policies")
    op.execute("DROP TRIGGER IF EXISTS model_price_append_only ON model_price")
    op.execute("DROP FUNCTION IF EXISTS reject_model_price_mutation()")
    op.execute("DROP TABLE IF EXISTS model_price")
