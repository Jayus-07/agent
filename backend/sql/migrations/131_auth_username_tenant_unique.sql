-- 131_auth_username_tenant_unique.sql — 用户名唯一性收窄到租户内（审计 P2 批次，2026-11-03）
-- 历史：008 建表时 username 为全局列级 UNIQUE（跨租户互占：A 租户注册 alice
-- 会占掉 B 租户的 alice，且"用户名已存在"文案构成全平台用户名枚举面）。
-- 审计 P2-2 要求查重与唯一性都带 tenant_id。应用层注册查重已同步加租户条件；
-- 本迁移收口 DB 语义。活库实测全局唯一约束可能已不在（初始化差异）——
-- 删除步骤幂等（有则删、无则跳过），最终态统一为 (tenant_id, username) 唯一。

-- 1) 删列级全局唯一约束（按列动态定位约束名，兼容 PG 自动命名差异）
DO $$
DECLARE
    c text;
BEGIN
    SELECT conname INTO c
    FROM pg_constraint
    WHERE conrelid = 'auth.users'::regclass
      AND contype = 'u'
      AND conkey @> ARRAY[(
          SELECT attnum::smallint FROM pg_attribute
          WHERE attrelid = 'auth.users'::regclass AND attname = 'username')];
    IF c IS NOT NULL THEN
        EXECUTE format('ALTER TABLE auth.users DROP CONSTRAINT %I', c);
    END IF;
END $$;

-- 2) 空租户存量收口到 default：tenant_id 为空的行永远不会被按租户登录命中
--    （登录/注册均要求可信 X-Tenant-Id），等同死账号；归入 default 使其可登录，
--    也让下方唯一索引不因 NULL（PG 唯一索引对 NULL 视为互异）而形同虚设。
UPDATE auth.users SET tenant_id = 'default'
WHERE tenant_id IS NULL OR tenant_id = '';

ALTER TABLE auth.users ALTER COLUMN tenant_id SET DEFAULT 'default';

-- 3) 租户内唯一（幂等）
CREATE UNIQUE INDEX IF NOT EXISTS uq_auth_users_tenant_username
    ON auth.users (tenant_id, username);
