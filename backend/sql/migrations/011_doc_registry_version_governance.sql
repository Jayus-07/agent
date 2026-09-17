-- 011_doc_registry_version_governance.sql — R4 版本治理：§6 治理 11 字段增量迁移
-- 目标库：agent_memory（与 010 同库同表 agent_memory.doc_registry）
-- 执行方式：
--   1) 容器内：psql -U postgres -d agent_memory -f /docker-migrations/011_doc_registry_version_governance.sql
--   2) 或依赖应用侧幂等补列（PostgresDocumentRegistry._ensure_columns，语句一致）
-- 幂等：ADD COLUMN IF NOT EXISTS，可重复执行。
--
-- §6 治理 11 字段 → 落库列映射（6 列已有、6 列新增）：
--   document_id          → doc_id（010 已有）
--   version_id           → version_id（本脚本新增）
--   content_hash         → file_hash（010 已有，内容 SHA256）
--   status               → status（010 已有）
--   effective_from       → effective_from（本脚本新增）
--   effective_to         → effective_to（本脚本新增，NULL = 现行版本）
--   supersedes_version_id → supersedes_version_id（本脚本新增，被取代的前版 doc_id）
--   source_priority      → source_priority（本脚本新增，来源权威级，越大越权威）
--   quality_status       → quality_status（本脚本新增，门禁裁决 unknown/pass/soft_warning/failed）
--   department / kb_id   → department / kb_id（010 已有）
--
-- SQLite 后端同步补列由 doc_registry._ensure_columns 惰性执行（双后端兼容，
-- 语义与类型逐列一致；R1 报告 §一.① 的 R4 依赖说明在此兑现）。

ALTER TABLE doc_registry ADD COLUMN IF NOT EXISTS version_id            TEXT DEFAULT '';
ALTER TABLE doc_registry ADD COLUMN IF NOT EXISTS effective_from        TEXT;
ALTER TABLE doc_registry ADD COLUMN IF NOT EXISTS effective_to          TEXT;
ALTER TABLE doc_registry ADD COLUMN IF NOT EXISTS supersedes_version_id TEXT DEFAULT '';
ALTER TABLE doc_registry ADD COLUMN IF NOT EXISTS source_priority       INTEGER DEFAULT 0;
ALTER TABLE doc_registry ADD COLUMN IF NOT EXISTS quality_status        TEXT DEFAULT 'unknown';

-- 存量行语义：version_id 为空 = 非版本链文档（无时效约束，检索期按现行处理）；
-- 版本链文档（如 rag_100_docs 的 policy_travel_v1/v2/v3）由入库脚本/上传链路
-- 填写 version_id + 生效窗口 + supersedes 链。日期一律 ISO 'YYYY-MM-DD' 文本，
-- 与 010 的时间列 TEXT 约定一致（字符串比较排序语义成立）。
