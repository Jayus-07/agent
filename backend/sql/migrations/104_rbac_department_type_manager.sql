-- RBAC 部门类型与一名部门主管，供客服团队分区和部门范围管理授权使用。

ALTER TABLE auth.departments
    ADD COLUMN IF NOT EXISTS department_type VARCHAR(32) NOT NULL DEFAULT 'general',
    ADD COLUMN IF NOT EXISTS manager_user_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_departments_department_type'
          AND conrelid = 'auth.departments'::regclass
    ) THEN
        ALTER TABLE auth.departments
            ADD CONSTRAINT ck_departments_department_type
            CHECK (department_type IN ('general', 'customer_service'));
    END IF;
END $$;

-- 041 号迁移中的 customer 编码是客服部的稳定种子编码。
UPDATE auth.departments
SET department_type = 'customer_service'
WHERE code = 'customer' AND department_type = 'general';

CREATE UNIQUE INDEX IF NOT EXISTS uq_departments_active_customer_service
    ON auth.departments (tenant_id)
    WHERE status = 1 AND department_type = 'customer_service';

CREATE INDEX IF NOT EXISTS idx_departments_manager
    ON auth.departments (tenant_id, manager_user_id)
    WHERE manager_user_id IS NOT NULL;

-- 收口历史客服绑定：只保留当前仍属于本租户启用客服部门的启用账号。
-- 未关联 auth.users 或账号/部门停用的档案关闭接单，需管理员在新客服团队页复核。
UPDATE customer_service.cs_agents a
SET enabled = FALSE, accepting = FALSE, updated_at = now()
WHERE a.enabled IS TRUE
  AND NOT EXISTS (
      SELECT 1
      FROM auth.users u
      JOIN auth.departments d
        ON d.tenant_id = u.tenant_id AND d.code = u.dept
      WHERE u.tenant_id = a.tenant_id
        AND CAST(u.id AS VARCHAR) = a.auth_user_id
        AND u.status = 1
        AND d.status = 1
        AND d.department_type = 'customer_service'
  );

COMMENT ON COLUMN auth.departments.department_type IS
    '部门业务类型；客服团队由客服部门类型识别，不依赖展示名称';
COMMENT ON COLUMN auth.departments.manager_user_id IS
    '该部门唯一主管用户；服务端确保同租户、在职且归属本部门';
