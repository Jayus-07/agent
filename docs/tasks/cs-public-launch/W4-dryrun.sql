-- W4 测试数据治理 dry-run（只读统计，未删除任何行）
-- cs-public-launch FINAL-PLAN §四 W4：按前缀白名单统计各表待清数量，供用户拍板。
-- 运行：docker exec -i agent-postgres-1 psql -U postgres -d agent_memory -f - < docs/tasks/cs-public-launch/W4-dryrun.sql
-- 说明：demo_fuzhou 真人实测数据（MO- 订单/钱包/S1-S9 会话）不在白名单，默认保留。

-- ── 白名单前缀（单一事实源；新增前缀只改这里） ──
CREATE TEMP TABLE _p(prefix text);
INSERT INTO _p VALUES ('p3-%'), ('p8r-%'), ('p7v-%'), ('eval_bot%'), ('uitest%'),
                       ('t1_train%'), ('mcp_gov%'), ('acc_verify%'), ('e2e_cs%'),
                       ('p7t-%'), ('w1-verify%'), ('w3-ws-offer%');

-- ── 1) 会话锚定表：conversation_id 命中任一前缀的行数 vs 全表行数 ──
WITH stats AS (
    SELECT 'customer_service.conversations'::text AS tbl,
           (SELECT count(*) FROM customer_service.conversations c
             WHERE EXISTS (SELECT 1 FROM _p WHERE c.conversation_id LIKE prefix)) AS matched,
           (SELECT count(*) FROM customer_service.conversations) AS total
    UNION ALL
    SELECT 'customer_service.messages',
           (SELECT count(*) FROM customer_service.messages m
             WHERE EXISTS (SELECT 1 FROM _p WHERE m.conversation_id LIKE prefix)),
           (SELECT count(*) FROM customer_service.messages)
    UNION ALL
    SELECT 'customer_service.handoffs',
           (SELECT count(*) FROM customer_service.handoffs h
             WHERE EXISTS (SELECT 1 FROM _p WHERE h.conversation_id LIKE prefix)),
           (SELECT count(*) FROM customer_service.handoffs)
    UNION ALL
    SELECT 'customer_service.assignments',
           (SELECT count(*) FROM customer_service.assignments a
             WHERE EXISTS (SELECT 1 FROM _p WHERE a.conversation_id LIKE prefix)),
           (SELECT count(*) FROM customer_service.assignments)
    UNION ALL
    SELECT 'customer_service.events',
           (SELECT count(*) FROM customer_service.events e
             WHERE EXISTS (SELECT 1 FROM _p WHERE e.conversation_id LIKE prefix)),
           (SELECT count(*) FROM customer_service.events)
    UNION ALL
    SELECT 'customer_service.confirmations',
           (SELECT count(*) FROM customer_service.confirmations f
             WHERE EXISTS (SELECT 1 FROM _p WHERE f.conversation_id LIKE prefix)),
           (SELECT count(*) FROM customer_service.confirmations)
    UNION ALL
    SELECT 'customer_service.complaints',
           (SELECT count(*) FROM customer_service.complaints c
             WHERE EXISTS (SELECT 1 FROM _p WHERE c.conversation_id LIKE prefix)),
           (SELECT count(*) FROM customer_service.complaints)
    UNION ALL
    SELECT 'customer_service.agent_actions',
           (SELECT count(*) FROM customer_service.agent_actions x
             WHERE EXISTS (SELECT 1 FROM _p WHERE x.conversation_id LIKE prefix)),
           (SELECT count(*) FROM customer_service.agent_actions)
    UNION ALL
    SELECT 'customer_service.audit_logs',
           (SELECT count(*) FROM customer_service.audit_logs l
             WHERE EXISTS (SELECT 1 FROM _p WHERE l.conversation_id LIKE prefix)),
           (SELECT count(*) FROM customer_service.audit_logs)
    UNION ALL
    SELECT 'customer_service.cs_qa_scores',
           (SELECT count(*) FROM customer_service.cs_qa_scores q
             WHERE EXISTS (SELECT 1 FROM _p WHERE q.conversation_id LIKE prefix)),
           (SELECT count(*) FROM customer_service.cs_qa_scores)
    UNION ALL
    SELECT 'customer_service.cs_case',
           (SELECT count(*) FROM customer_service.cs_case s
             WHERE EXISTS (SELECT 1 FROM _p WHERE s.conversation_id LIKE prefix)),
           (SELECT count(*) FROM customer_service.cs_case)
    UNION ALL
    SELECT 'customer_service.cs_case_snapshots',
           (SELECT count(*) FROM customer_service.cs_case_snapshots n
             WHERE EXISTS (SELECT 1 FROM _p WHERE n.conversation_id LIKE prefix)),
           (SELECT count(*) FROM customer_service.cs_case_snapshots)
    UNION ALL
    SELECT 'ai.unanswered_questions',
           (SELECT count(*) FROM ai.unanswered_questions u
             WHERE EXISTS (SELECT 1 FROM _p WHERE u.session_id LIKE prefix)),
           (SELECT count(*) FROM ai.unanswered_questions)
    UNION ALL
    SELECT 'ai.trace_records',
           (SELECT count(*) FROM ai.trace_records t
             WHERE EXISTS (SELECT 1 FROM _p WHERE t.conversation_id LIKE prefix)),
           (SELECT count(*) FROM ai.trace_records)
    UNION ALL
    SELECT 'ai.agent_tasks',
           (SELECT count(*) FROM ai.agent_tasks t
             WHERE EXISTS (SELECT 1 FROM _p WHERE t.session_id LIKE prefix)),
           (SELECT count(*) FROM ai.agent_tasks)
)
SELECT tbl, matched, total,
       CASE WHEN total > 0 THEN round(matched * 100.0 / total, 1) ELSE 0 END AS pct
FROM stats
ORDER BY matched DESC;

-- ── 2) 测试租户整行（tenant-p7t* / tenant-demo） ──
SELECT 'customer_service.conversations' AS tbl, tenant_id, count(*) AS rows
FROM customer_service.conversations
WHERE tenant_id IN ('tenant-p7t', 'tenant-demo', 'tenant-p7t-other')
GROUP BY tenant_id
UNION ALL
SELECT 'ai.unanswered_questions', tenant_id, count(*)
FROM ai.unanswered_questions
WHERE tenant_id IN ('tenant-p7t', 'tenant-demo', 'tenant-p7t-other')
GROUP BY tenant_id
ORDER BY 1, 2;
