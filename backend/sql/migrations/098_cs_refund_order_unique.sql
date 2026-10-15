-- 098_cs_refund_order_unique.sql — 跨会话活跃退款申请按租户/用户/订单唯一。
-- 先做只读重复组检查；若历史数据冲突，事务终止且不修改任何案件，须人工诊断处置。
-- 安全诊断只返回重复组数量，不输出订单号或用户标识：
-- SELECT COUNT(*) FROM (
--   SELECT tenant_id, user_id,
--          COALESCE(NULLIF(BTRIM(context #>> '{refund_request,order_id}'), ''), '') AS order_key
--     FROM customer_service.cs_case
--    WHERE case_type = 'refund'
--      AND status IN ('open', 'waiting_user', 'processing')
-- GROUP BY tenant_id, user_id,
--          COALESCE(NULLIF(BTRIM(context #>> '{refund_request,order_id}'), ''), '')
--   HAVING COUNT(*) > 1
-- ) AS duplicate_refund_orders;
DO $$
DECLARE
    duplicate_groups BIGINT;
BEGIN
    SELECT COUNT(*)
      INTO duplicate_groups
      FROM (
          SELECT tenant_id,
                 user_id,
                 COALESCE(
                     NULLIF(BTRIM(context #>> '{refund_request,order_id}'), ''),
                     ''
                 ) AS order_key
            FROM customer_service.cs_case
           WHERE case_type = 'refund'
             AND status IN ('open', 'waiting_user', 'processing')
        GROUP BY tenant_id,
                 user_id,
                 COALESCE(
                     NULLIF(BTRIM(context #>> '{refund_request,order_id}'), ''),
                     ''
                 )
          HAVING COUNT(*) > 1
      ) AS duplicate_refund_orders;

    IF duplicate_groups > 0 THEN
        RAISE EXCEPTION
            '098_cs_refund_order_unique found % duplicate active refund groups; no rows were changed',
            duplicate_groups
            USING HINT = 'Run a read-only duplicate-group diagnostic and resolve records through the approved case workflow before retrying.';
    END IF;
END $$;

-- NULL、缺失及空白订单 ID 统一为一个键，不能借此绕过唯一性。
CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_case_active_refund_order
    ON customer_service.cs_case (
        tenant_id,
        user_id,
        (COALESCE(
            NULLIF(BTRIM(context #>> '{refund_request,order_id}'), ''),
            ''
        ))
    )
    WHERE case_type = 'refund'
      AND status IN ('open', 'waiting_user', 'processing');
