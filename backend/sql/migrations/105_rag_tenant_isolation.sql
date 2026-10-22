-- 105_rag_tenant_isolation.sql — 将 RAG 文档与操作历史明确绑定可信租户。
-- 旧数据按当前单租户部署归入 default；新数据由认证身份写入 tenant_id。

ALTER TABLE doc_registry
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';
UPDATE doc_registry SET tenant_id = 'default'
WHERE tenant_id IS NULL OR tenant_id = '';
CREATE INDEX IF NOT EXISTS idx_doc_registry_tenant_doc
    ON doc_registry(tenant_id, doc_id);

ALTER TABLE doc_operation_log
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';
UPDATE doc_operation_log SET tenant_id = 'default'
WHERE tenant_id IS NULL OR tenant_id = '';
CREATE INDEX IF NOT EXISTS idx_dop_tenant_created
    ON doc_operation_log(tenant_id, created_at DESC);

UPDATE rag_index_runs SET tenant_id = 'default'
WHERE tenant_id IS NULL OR tenant_id = '';
ALTER TABLE rag_index_runs ALTER COLUMN tenant_id SET DEFAULT 'default';
