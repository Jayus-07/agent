-- 082_travel_plan_tenant_scope.sql — 行程版本账本补齐租户与用户复合隔离
--
-- 旧版本表仅以 conversation_id + plan_version 作主键，查询也只过滤 user_id。
-- 该迁移给存量记录绑定 default 租户，并允许不同租户/用户安全复用会话 ID。
-- 服务端所有读写仍同时按 tenant_id、user_id、conversation_id 收窄。

ALTER TABLE IF EXISTS travel_plan_versions
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

DO $$
DECLARE
    table_oid REGCLASS := to_regclass('travel_plan_versions');
    old_pk TEXT;
BEGIN
    IF table_oid IS NOT NULL THEN
        SELECT conname INTO old_pk
        FROM pg_constraint
        WHERE conrelid = table_oid AND contype = 'p';

        IF old_pk IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = table_oid AND contype = 'p'
              AND pg_get_constraintdef(oid) LIKE '%tenant_id%'
              AND pg_get_constraintdef(oid) LIKE '%user_id%'
        ) THEN
            EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',
                           table_oid, old_pk);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = table_oid AND contype = 'p'
              AND pg_get_constraintdef(oid) LIKE '%tenant_id%'
              AND pg_get_constraintdef(oid) LIKE '%user_id%'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT travel_plan_versions_pkey '
                'PRIMARY KEY (tenant_id, user_id, conversation_id, plan_version)',
                table_oid);
        END IF;
    END IF;
END $$;
