-- ============================================================
-- 041_auth_departments.sql — 部门主数据（P3，2026-09-23 授权生产收口）
--
-- 目标库：agent_memory
-- 背景：auth.users.dept 此前是自由字符串（创建/更新均无校验，管理员可写
-- "finance123xxx"），前端部门列表硬编码。本迁移建立租户内部门主数据权威：
--   - RBAC 修改/创建用户 dept 必须命中本表 active 记录（或显式清空）；
--   - KB 授权矩阵（config/knowledge_base.owner_depts）继续以 code 关联，
--     本阶段不迁移 KB 定义，避免破坏已验证授权语义。
-- 幂等：CREATE IF NOT EXISTS + seed ON CONFLICT DO NOTHING，可安全重放；
-- 存量用户 dept='' 不受影响（空 = 未分配，保持最小权限语义，不偷偷回填）。
-- ============================================================

CREATE TABLE IF NOT EXISTS auth.departments (
    id         BIGSERIAL PRIMARY KEY,
    tenant_id  VARCHAR(64)  NOT NULL DEFAULT 'default',
    code       VARCHAR(50)  NOT NULL,
    name       VARCHAR(100) NOT NULL DEFAULT '',
    status     SMALLINT     NOT NULL DEFAULT 1,   -- 1=启用 0=停用
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_departments_tenant_code UNIQUE (tenant_id, code)
);

CREATE INDEX IF NOT EXISTS idx_departments_tenant_status
    ON auth.departments (tenant_id, status);

-- 种子：与 config/knowledge_base.DEPARTMENTS 对齐（单一事实源仍在代码，
-- DB 是管理面校验权威；新增部门走 INSERT 或后续迁移，不在业务代码写死）
INSERT INTO auth.departments (tenant_id, code, name) VALUES
    ('default', 'warehouse',    '仓储部'),
    ('default', 'supply_chain', '供应链部'),
    ('default', 'order_dept',   '订单部'),
    ('default', 'customer',     '客服部'),
    ('default', 'product_dept', '商品部'),
    ('default', 'hr',           '人事部'),
    ('default', 'finance',      '财务部'),
    ('default', 'admin',        '行政部'),
    ('default', 'general',      '通用')
ON CONFLICT (tenant_id, code) DO NOTHING;
