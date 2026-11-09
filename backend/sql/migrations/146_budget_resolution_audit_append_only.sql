-- 146: 核销审计表 append-only DB 级守卫（补齐 P3-1）
-- 日期：2026-11-10
--
-- 背景：144 的注释与文档宣称 budget_reservation_resolutions 为
-- append-only，但只有应用层纪律（resolve_needs_review 只 INSERT），
-- 没有物理约束——任何持写权限的连接都能改写/删除审计行，破坏
-- "留痕可追溯，不物理删除"的契约。仿 053（model_price immutable
-- trigger）先例补齐 DB 级守卫；审计行不允许任何形式的改写。
--
-- 注意：幂等（DROP IF EXISTS + CREATE）；存量 121 行全部有效，
-- 无需回填。

CREATE OR REPLACE FUNCTION reject_budget_resolution_audit_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'budget_reservation_resolutions is append-only（核销审计行不允许 UPDATE/DELETE）';
END;
$$;

DROP TRIGGER IF EXISTS trg_budget_resolution_audit_append_only
    ON budget_reservation_resolutions;
CREATE TRIGGER trg_budget_resolution_audit_append_only
    BEFORE UPDATE OR DELETE ON budget_reservation_resolutions
    FOR EACH ROW EXECUTE FUNCTION reject_budget_resolution_audit_mutation();
