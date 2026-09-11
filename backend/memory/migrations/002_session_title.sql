-- memory/migrations/002_session_title.sql
-- 会话标题与 L2 摘要分字段
-- 历史背景：summary 曾同时承担「侧栏标题」和「L2 自动摘要」两个职责
-- （rename_session 写它当标题，needs_summarization 后 update_summary 写它当摘要），
-- 导致重命名会话的摘要注入失效、长摘要被当标题展示。
-- Run: psql -h localhost -U postgres -d demo -f memory/migrations/002_session_title.sql

ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS title VARCHAR(128);

-- 旧数据回填：title 为空的行从 summary 取值截断。
-- 旧的重命名标题（短文本）完整保留；旧的 L2 长摘要截断成可读标题
UPDATE chat_sessions
SET title = LEFT(summary, 128)
WHERE title IS NULL AND summary IS NOT NULL AND summary <> '';
