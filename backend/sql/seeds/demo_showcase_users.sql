-- ═══════════════════════════════════════════════════════════════════
-- demo_showcase_users.sql — 统一演示数据脚本（2026-10-08 验收修复批）
--
-- 用途：CS_DEMO_MODE=false（真实身份隔离）后，按真实 auth 账号生成
-- 可演示的订单/退款/投诉/客服会话/长期画像数据。新注册用户零数据，
-- 只有本脚本生成的演示账号能展示完整数据链路。
--
-- ⚠️ 跨库执行（auth 在 agent_memory，业务表在 agent_business）：
--   第 1 步（agent_memory）：建演示账号 + 客服会话/投诉/画像
--   第 2 步（agent_business）：按第 1 步打出的 uid 造业务数据
--
-- 演示账号（统一密码 Demo@2026，演示后请改密或禁用）：
--   demo_fuzhou  / demo_li  / demo_wang
--
-- 幂等性：全部 ON CONFLICT / WHERE NOT EXISTS，可重复执行。
-- 与 demo_sandbox.sql 的 99001 体系互不影响（勿在同一账号混用）。
-- ═══════════════════════════════════════════════════════════════════

-- ═══════════════════════════════════════════════════════════════════
-- 第 1 步：agent_memory 库
-- 执行：docker exec -i agent-postgres-1 psql -U postgres -d agent_memory < 本文件
-- ═══════════════════════════════════════════════════════════════════

-- 1.1 演示账号（password_hash = pbkdf2_sha256(200000, "Demo@2026")，local_jwt.py 的 hex 格式）
-- tenant_id 必填：登录按 (username, tenant_id) 查询，缺省 NULL 将永远 400（2026-10-08 服务器验收实测）
INSERT INTO auth.users (username, tenant_id, password_hash, real_name, dept, status)
VALUES
  ('demo_fuzhou', 'default', 'pbkdf2_sha256$200000$030187a9c884162a228920e410c4581f$8b764558115caec7b4f51092a8c4637973d53faf94fdd9a103ae459a8df7f517', '演示·福州行', 'general', 1),
  ('demo_li',     'default', 'pbkdf2_sha256$200000$030187a9c884162a228920e410c4581f$8b764558115caec7b4f51092a8c4637973d53faf94fdd9a103ae459a8df7f517', '演示·李经理', 'general', 1),
  ('demo_wang',   'default', 'pbkdf2_sha256$200000$030187a9c884162a228920e410c4581f$8b764558115caec7b4f51092a8c4637973d53faf94fdd9a103ae459a8df7f517', '演示·王同学', 'general', 1)
ON CONFLICT (username) DO NOTHING;

-- 登记角色：auth.users.role 默认即 viewer（009），演示账号无需提权；
-- 如需演示 sql.query 等需权限能力，可显式：
--   UPDATE auth.users SET role='editor' WHERE username LIKE 'demo_%';

-- 1.2 客服会话 + 消息（每账号 1-2 条，会话 ID 与账号 uid 绑定）
--     conversation_id 前缀 DEMO-SHOW-，与真实会话永不冲突
INSERT INTO customer_service.conversations
  (conversation_id, user_id, tenant_id, conversation_status, channel, created_at, updated_at)
SELECT v.conversation_id, u.id::text, 'default', v.status, 'web',
       v.at, v.at
FROM (VALUES
  ('DEMO-SHOW-C1', 'demo_fuzhou', 'resolved', NOW() - INTERVAL '6 days'),
  ('DEMO-SHOW-C2', 'demo_fuzhou', 'resolved', NOW() - INTERVAL '2 days'),
  ('DEMO-SHOW-C3', 'demo_li',     'resolved', NOW() - INTERVAL '4 days')
) AS v(conversation_id, username, status, at)
JOIN auth.users u ON u.username = v.username
WHERE NOT EXISTS (
  SELECT 1 FROM customer_service.conversations c
  WHERE c.conversation_id = v.conversation_id
);

INSERT INTO customer_service.messages
  (message_id, conversation_id, role, sender_type, content, intent_domain, intent_name, created_at)
SELECT v.message_id, v.conversation_id, v.role, v.role, v.content,
       'customer_service', v.intent, v.at
FROM (VALUES
  ('DEMO-SHOW-M01', 'DEMO-SHOW-C1', 'user',      '我上周买的蓝牙耳机有电流声，想申请退款',   'as_refund',  NOW() - INTERVAL '6 days'),
  ('DEMO-SHOW-M02', 'DEMO-SHOW-C1', 'assistant', '您好，已为您找到符合条件的订单，退款将在1-3个工作日原路退回。', 'as_refund', NOW() - INTERVAL '6 days' + INTERVAL '1 minute'),
  ('DEMO-SHOW-M03', 'DEMO-SHOW-C2', 'user',      '物流显示发货十天了还没到，太慢了',       'complaint',  NOW() - INTERVAL '2 days'),
  ('DEMO-SHOW-M04', 'DEMO-SHOW-C2', 'assistant', '非常抱歉给您带来不好的体验，已为您记录物流投诉并跟进。', 'complaint', NOW() - INTERVAL '2 days' + INTERVAL '1 minute'),
  ('DEMO-SHOW-M05', 'DEMO-SHOW-C3', 'user',      '查一下我最近有哪些订单',                 'as_query',   NOW() - INTERVAL '4 days'),
  ('DEMO-SHOW-M06', 'DEMO-SHOW-C3', 'assistant', '已为您列出近期订单，共 6 条，其中 1 条已退款。', 'as_query', NOW() - INTERVAL '4 days' + INTERVAL '1 minute')
) AS v(message_id, conversation_id, role, content, intent, at)
WHERE NOT EXISTS (
  SELECT 1 FROM customer_service.messages m
  WHERE m.message_id = v.message_id
);

-- 1.3 投诉工单（挂会话）
INSERT INTO customer_service.complaints
  (complaint_id, user_id, conversation_id, category, severity, description, status, created_at)
SELECT v.complaint_id, u.id::text, v.conversation_id, v.category, v.severity,
       v.description, v.status, v.at
FROM (VALUES
  ('DEMO-SHOW-COMPLAINT-1', 'demo_fuzhou', 'DEMO-SHOW-C2', 'logistics',      'medium', '物流延迟十天未发货', 'resolved', NOW() - INTERVAL '2 days'),
  ('DEMO-SHOW-COMPLAINT-2', 'demo_fuzhou', 'DEMO-SHOW-C1', 'refund_dispute', 'low',    '退款进度咨询',       'closed',   NOW() - INTERVAL '6 days')
) AS v(complaint_id, username, conversation_id, category, severity, description, status, at)
JOIN auth.users u ON u.username = v.username
WHERE NOT EXISTS (
  SELECT 1 FROM customer_service.complaints c
  WHERE c.complaint_id = v.complaint_id
);

-- 1.4 长期画像（设置页 GET /api/memory/profile 展示；explicit 不衰减）
INSERT INTO public.memory_records
  (id, tenant_id, user_id, session_id, memory_type, content, memory_key,
   importance_score, confidence_score, origin, access_count, is_active,
   created_at, last_access_at)
SELECT gen_random_uuid(), 'default', u.id::text, '', v.memory_type, v.content, v.memory_key,
       v.importance, 0.98, 'explicit', 3, true, v.at, v.at
FROM (VALUES
  ('demo_fuzhou', 'preference', '偏好用中文交流',                         'response.language',  0.9, NOW() - INTERVAL '20 days'),
  ('demo_fuzhou', 'user_fact',  '常居福建福州，关注周末周边游',            'profile.location',   0.85, NOW() - INTERVAL '15 days'),
  ('demo_fuzhou', 'preference', '旅游喜欢轻松节奏，不喜欢太赶',            'travel.pace',        0.8, NOW() - INTERVAL '10 days'),
  ('demo_li',     'user_fact',  '从事新能源汽车行业市场分析工作',          'job.role',           0.85, NOW() - INTERVAL '18 days'),
  ('demo_li',     'preference', '看数据喜欢表格形式，不要大段文字',        'report.format',      0.7, NOW() - INTERVAL '12 days'),
  ('demo_wang',   'preference', '购物习惯货比三家，常先咨询客服再下单',    'shopping.habit',     0.65, NOW() - INTERVAL '8 days')
) AS v(username, memory_type, content, memory_key, importance, at)
JOIN auth.users u ON u.username = v.username
WHERE NOT EXISTS (
  SELECT 1 FROM public.memory_records r
  WHERE r.tenant_id = 'default' AND r.user_id = u.id::text
    AND r.memory_key = v.memory_key AND r.is_active
);

-- 1.5 打出 uid 映射（第 2 步要用）
SELECT username, id AS uid FROM auth.users
WHERE username IN ('demo_fuzhou', 'demo_li', 'demo_wang')
ORDER BY username;

-- ═══════════════════════════════════════════════════════════════════
-- 第 2 步：agent_business 库（把上面的 uid 代入 :demo_fuzhou_uid 等变量）
-- 执行：docker exec -i agent-postgres-1 psql -U postgres -d agent_business \
--        -v demo_fuzhou_uid=<上一步的数字> -v demo_li_uid=<...> -v demo_wang_uid=<...> \
--        < demo_business.sql 段
-- ═══════════════════════════════════════════════════════════════════
-- （该段单独保存为 demo_showcase_business.sql，见同目录）
