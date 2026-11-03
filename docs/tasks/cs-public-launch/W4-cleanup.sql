-- W4 测试数据治理：幂等清理脚本（唯一一次写库，执行前逐表回显计数）
-- 拍板：A+B 全清（用户 2026-11-04 确认）。demo_fuzhou(872) 真人实测数据保留。
-- 运行方式：分段执行（每段一条 psql -c 或事务），或整文件 docker exec -i ... psql -f -。
-- 安全边界：不触碰 auth.users / auth.* 任何表；逐段 BEGIN/COMMIT，失败即回滚该段。
-- 幂等性：全部按确定性条件删除（前缀 / e2e 用户名下 / 合成用户），重跑计数为 0。

-- ══════════ 圈定范围（与 dry-run 同一口径） ══════════
-- A 类：前缀白名单（12 前缀）
-- B 类：cs_eval_ / eval-cs- / sse-gate-resume-session- 前缀
--      + e2e 测试账号名下全部会话（auth.users.username 匹配）
--      + 合成用户 eval_bot / 99001 名下全部会话
-- 锚定临时表：一次圈定，从属表按 conversation_id 关联删除。
CREATE TEMP TABLE _targets AS
SELECT c.conversation_id
FROM customer_service.conversations c
WHERE c.conversation_id LIKE 'p3-%' OR c.conversation_id LIKE 'p8r-%'
   OR c.conversation_id LIKE 'p7v-%' OR c.conversation_id LIKE 'eval_bot%'
   OR c.conversation_id LIKE 'uitest%' OR c.conversation_id LIKE 't1_train%'
   OR c.conversation_id LIKE 'mcp_gov%' OR c.conversation_id LIKE 'acc_verify%'
   OR c.conversation_id LIKE 'e2e_cs%' OR c.conversation_id LIKE 'p7t-%'
   OR c.conversation_id LIKE 'w1-verify%' OR c.conversation_id LIKE 'w3-ws-offer%'
   OR c.conversation_id LIKE 'cs_eval_%' OR c.conversation_id LIKE 'eval-cs-%'
   OR c.conversation_id LIKE 'sse-gate-resume-session-%'
   OR c.user_id IN ('eval_bot', '99001')
   OR EXISTS (SELECT 1 FROM auth.users u
              WHERE u.id::text = c.user_id
                AND (u.username LIKE 'e2e%' OR u.username LIKE 'cs_p35%'))
   OR c.tenant_id IN ('tenant-p7t', 'tenant-demo', 'tenant-p7t-other');

-- ══════════ 执行前计数（应与 dry-run + B 类统计吻合） ══════════
SELECT 'targets(会话数)' AS stage, count(*)::text AS n FROM _targets
UNION ALL SELECT 'messages',  count(*)::text FROM customer_service.messages  m JOIN _targets t USING (conversation_id)
UNION ALL SELECT 'handoffs',  count(*)::text FROM customer_service.handoffs  h JOIN _targets t USING (conversation_id)
UNION ALL SELECT 'assignments', count(*)::text FROM customer_service.assignments a JOIN _targets t USING (conversation_id)
UNION ALL SELECT 'events',    count(*)::text FROM customer_service.events    e JOIN _targets t USING (conversation_id)
UNION ALL SELECT 'confirmations', count(*)::text FROM customer_service.confirmations f JOIN _targets t USING (conversation_id)
UNION ALL SELECT 'complaints', count(*)::text FROM customer_service.complaints c JOIN _targets t USING (conversation_id)
UNION ALL SELECT 'agent_actions', count(*)::text FROM customer_service.agent_actions x JOIN _targets t USING (conversation_id)
UNION ALL SELECT 'audit_logs', count(*)::text FROM customer_service.audit_logs l JOIN _targets t USING (conversation_id)
UNION ALL SELECT 'cs_qa_scores', count(*)::text FROM customer_service.cs_qa_scores q JOIN _targets t USING (conversation_id)
UNION ALL SELECT 'cs_case',   count(*)::text FROM customer_service.cs_case s JOIN _targets t USING (conversation_id)
UNION ALL SELECT 'cs_case_snapshots', count(*)::text FROM customer_service.cs_case_snapshots n JOIN _targets t USING (conversation_id)
UNION ALL SELECT 'tickets',   count(*)::text FROM customer_service.tickets k JOIN _targets t USING (conversation_id)
UNION ALL SELECT 'ai.unanswered_questions(by session)', count(*)::text FROM ai.unanswered_questions u JOIN customer_service.conversations c ON c.conversation_id = u.session_id JOIN _targets t ON t.conversation_id = c.conversation_id
UNION ALL SELECT 'ai.trace_records', count(*)::text FROM ai.trace_records r JOIN _targets t USING (conversation_id)
UNION ALL SELECT 'ai.agent_tasks(by session)', count(*)::text FROM ai.agent_tasks k2 JOIN customer_service.conversations c ON c.conversation_id = k2.session_id JOIN _targets t ON t.conversation_id = c.conversation_id
ORDER BY 1;

-- ══════════ 分段删除（每段独立事务，回显删除行数） ══════════
-- 段 1：无 FK 指向 conversations 的从属表先行
BEGIN;
WITH del AS (DELETE FROM customer_service.cs_qa_scores q USING _targets t WHERE q.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted cs_qa_scores' AS step, count(*) FROM del;
COMMIT;

BEGIN;
WITH del AS (DELETE FROM customer_service.audit_logs l USING _targets t WHERE l.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted audit_logs' AS step, count(*) FROM del;
COMMIT;

BEGIN;
WITH del AS (DELETE FROM customer_service.cs_case s USING _targets t WHERE s.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted cs_case' AS step, count(*) FROM del;
COMMIT;

BEGIN;
WITH del AS (DELETE FROM customer_service.cs_case_snapshots n USING _targets t WHERE n.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted cs_case_snapshots' AS step, count(*) FROM del;
COMMIT;

BEGIN;
WITH del AS (DELETE FROM customer_service.tickets k USING _targets t WHERE k.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted tickets' AS step, count(*) FROM del;
COMMIT;

BEGIN;
WITH del AS (DELETE FROM ai.unanswered_questions WHERE tenant_id IN ('tenant-p7t', 'tenant-demo', 'tenant-p7t-other') RETURNING 1)
SELECT 'deleted ai.unanswered_questions(test tenants)' AS step, count(*) FROM del;
COMMIT;

BEGIN;
WITH del AS (DELETE FROM ai.trace_records r USING _targets t WHERE r.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted ai.trace_records' AS step, count(*) FROM del;
COMMIT;

BEGIN;
WITH del AS (DELETE FROM ai.unanswered_questions u
             USING customer_service.conversations c, _targets t
             WHERE u.session_id = c.conversation_id AND c.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted ai.unanswered_questions(session anchored)' AS step, count(*) FROM del;
COMMIT;

BEGIN;
WITH del AS (DELETE FROM ai.agent_tasks k2
             USING customer_service.conversations c, _targets t
             WHERE k2.session_id = c.conversation_id AND c.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted ai.agent_tasks(session anchored)' AS step, count(*) FROM del;
COMMIT;

-- 段 2：FK 指向 conversations 的从属表（messages/agent_actions/complaints/confirmations/assignments）
BEGIN;
WITH del AS (DELETE FROM customer_service.messages m USING _targets t WHERE m.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted messages' AS step, count(*) FROM del;
COMMIT;

BEGIN;
WITH del AS (DELETE FROM customer_service.agent_actions x USING _targets t WHERE x.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted agent_actions' AS step, count(*) FROM del;
COMMIT;

BEGIN;
WITH del AS (DELETE FROM customer_service.complaints c USING _targets t WHERE c.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted complaints' AS step, count(*) FROM del;
COMMIT;

BEGIN;
WITH del AS (DELETE FROM customer_service.confirmations f USING _targets t WHERE f.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted confirmations' AS step, count(*) FROM del;
COMMIT;

BEGIN;
WITH del AS (DELETE FROM customer_service.assignments a USING _targets t WHERE a.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted assignments' AS step, count(*) FROM del;
COMMIT;

-- 段 3：handoffs（其上无指向它的 FK 依赖 assignments 之外；assignments 已先行）
BEGIN;
WITH del AS (DELETE FROM customer_service.handoffs h USING _targets t WHERE h.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted handoffs' AS step, count(*) FROM del;
COMMIT;

-- 段 4：事件与锚定表 conversations（最后删）
BEGIN;
WITH del AS (DELETE FROM customer_service.events e USING _targets t WHERE e.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted events' AS step, count(*) FROM del;
COMMIT;

BEGIN;
WITH del AS (DELETE FROM customer_service.conversations c USING _targets t WHERE c.conversation_id = t.conversation_id RETURNING 1)
SELECT 'deleted conversations' AS step, count(*) FROM del;
COMMIT;

-- ══════════ 保留性自检（应全部 >0：demo 实测与真实账号不受影响） ══════════
SELECT 'demo_fuzhou conversations 保留' AS check_item, count(*)::text AS n
FROM customer_service.conversations WHERE user_id = '872'
UNION ALL
SELECT '剩余 open 会话', count(*)::text FROM customer_service.conversations WHERE conversation_status='open';
