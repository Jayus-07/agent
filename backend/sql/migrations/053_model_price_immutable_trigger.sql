-- 053_model_price_immutable_trigger.sql — 价格表追加式约束补齐
-- 031 已声明拒绝变价函数，但历史迁移漏挂 trigger，导致 UPDATE 可绕过
-- 追加式契约。本迁移对存量库和新库均幂等补齐 DB 级守卫。

CREATE OR REPLACE FUNCTION reject_model_price_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.effective_to IS NULL
       AND NEW.effective_to IS NOT NULL
       AND NEW.effective_to >= OLD.effective_from
       AND OLD.model_name          IS NOT DISTINCT FROM NEW.model_name
       AND OLD.component           IS NOT DISTINCT FROM NEW.component
       AND OLD.dimension           IS NOT DISTINCT FROM NEW.dimension
       AND OLD.price_per_unit      IS NOT DISTINCT FROM NEW.price_per_unit
       AND OLD.unit                IS NOT DISTINCT FROM NEW.unit
       AND OLD.currency            IS NOT DISTINCT FROM NEW.currency
       AND OLD.price_table_version IS NOT DISTINCT FROM NEW.price_table_version
       AND OLD.source              IS NOT DISTINCT FROM NEW.source
       AND OLD.effective_from      IS NOT DISTINCT FROM NEW.effective_from
       AND OLD.approval_status     IS NOT DISTINCT FROM NEW.approval_status
       AND OLD.reviewer_1          IS NOT DISTINCT FROM NEW.reviewer_1
       AND OLD.reviewer_2          IS NOT DISTINCT FROM NEW.reviewer_2
       AND OLD.created_at          IS NOT DISTINCT FROM NEW.created_at
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'model_price is append-only (只允许关闭 effective_to，不允许改价或删行)';
END;
$$;

DROP TRIGGER IF EXISTS trg_model_price_immutable ON model_price;
CREATE TRIGGER trg_model_price_immutable
    BEFORE UPDATE OR DELETE ON model_price
    FOR EACH ROW EXECUTE FUNCTION reject_model_price_mutation();
