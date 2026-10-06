-- 078_eval_run_validity.sql — 评测 run 有效性列（P0-04：区分「质量失败」与「环境故障」）
-- （2026-10-07 Eval 可信度收口；裁决唯一出口 backend/evaluation/validity.py）
--
-- 口径：
-- * validity = VALID / INVALID_ENV / INVALID_PROVIDER / INVALID_BUDGET /
--   INVALID_DATASET / INVALID_INFRA（与 RunValidity 枚举严格一致）。
-- * 环境无效（如 45/45 "请求预算已超限: request_fallbacks"）的 run 不得在
--   管理端显示成真实质量 0%、不得作为 Release Gate 通过证据、不得更新
--   baseline——消费方（列表 API / Gate / 前端徽章）按本列渲染。
-- * 历史 run 无裁决数据 → 默认 VALID（旧行为兼容）；需要复核时由
--   classify_report_validity 对旧 report 现算补录。
--
-- 回滚：
--   ALTER TABLE ai.eval_run_records DROP COLUMN IF EXISTS invalid_reason;
--   ALTER TABLE ai.eval_run_records DROP COLUMN IF EXISTS validity;

ALTER TABLE ai.eval_run_records
    ADD COLUMN IF NOT EXISTS validity TEXT NOT NULL DEFAULT 'VALID';
ALTER TABLE ai.eval_run_records
    ADD COLUMN IF NOT EXISTS invalid_reason TEXT NOT NULL DEFAULT '';
