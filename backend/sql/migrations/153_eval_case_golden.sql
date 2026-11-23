-- 评测中心批次 6a：Golden 标注表（逐题标杆标记，治理行为与用例内容修订分离）。
-- 标注 append-only 留历史（谁/何时/为何）；读侧取各 case 最新一条。
-- 不触 eval_case_revisions：revision content_hash 覆盖 metadata_json，
-- 直改已发布修订的 metadata 等于破坏内容锁（见 repository revision_content）。

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_case_golden (
    mark_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   VARCHAR(128) NOT NULL,
    case_id     UUID NOT NULL,
    golden      BOOLEAN NOT NULL,
    reason      VARCHAR(1000) NOT NULL DEFAULT '',
    marked_by   VARCHAR(128) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, case_id)
        REFERENCES evaluation_v2.eval_cases (tenant_id, case_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS eval_case_golden_case_latest_idx
    ON evaluation_v2.eval_case_golden (tenant_id, case_id, created_at DESC);

-- 租户 RLS：与 108 迁移的 V2 表同口径（tenant_scope policy）
DROP POLICY IF EXISTS tenant_scope ON evaluation_v2.eval_case_golden;
CREATE POLICY tenant_scope ON evaluation_v2.eval_case_golden
    USING (tenant_id = evaluation_v2.current_tenant_id())
    WITH CHECK (tenant_id = evaluation_v2.current_tenant_id());

GRANT SELECT, INSERT ON evaluation_v2.eval_case_golden TO evaluation_v2_app;
