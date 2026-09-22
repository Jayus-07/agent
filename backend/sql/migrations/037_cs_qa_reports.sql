-- 037_cs_qa_reports.sql
-- 批次D：客服质检每日报表。beat 每日聚合前一天运营指标写入本表，
-- 管理端只读查询。指标走 JSONB（结构还会演进，不建宽表）。

CREATE SCHEMA IF NOT EXISTS customer_service;

CREATE TABLE IF NOT EXISTS customer_service.qa_daily_reports (
    id            BIGSERIAL PRIMARY KEY,
    report_date   DATE NOT NULL,
    tenant_id     VARCHAR(64) NOT NULL DEFAULT 'default',
    metrics       JSONB NOT NULL DEFAULT '{}'::jsonb,
    generated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 日报幂等：同租户同日期唯一，重复执行覆盖（beat 重跑安全）
CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_qa_reports_date
    ON customer_service.qa_daily_reports (tenant_id, report_date);

CREATE INDEX IF NOT EXISTS idx_cs_qa_reports_range
    ON customer_service.qa_daily_reports (tenant_id, report_date DESC);
