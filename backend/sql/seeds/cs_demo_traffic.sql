-- ============================================================
-- cs_demo_traffic.sql — 客服演示流量播种（质检/统计页数据源）
-- 日期: 2026-09-22（批次四能力 MVP 补数据）
--
-- ⚠️ 全部为模拟数据，仅供演示与质检日报验证：
--   - conversation_id 统一 DEMO-CONV- 前缀，message_id 统一 DEMO-MSG- 前缀
--   - 幂等：先删后插（仅限 DEMO- 前缀，不触碰真实数据）
--   - 数据落在近 7 天，质检日报（每日 06:10）可立即出数
--
-- 覆盖:
--   - 会话 ×8（AI 5 / 人工 2 / 混合 1），含满意度评分 6 条
--   - 消息 ×30+，user 消息带 intent_name（order/logistics/refund/
--     complaint/knowledge/product），喂意图 Top10 统计
--   - 人工会话绑定坐席 cs_wire_e2e（喂坐席维度质量）
-- 执行（幂等可重跑）:
--   docker exec -i agent-postgres-1 psql -U postgres -d agent_memory \
--     < backend/sql/seeds/cs_demo_traffic.sql
-- ============================================================

BEGIN;

-- 0. 清理旧演示流量（带前缀守卫）
DELETE FROM customer_service.messages
 WHERE conversation_id LIKE 'DEMO-CONV-%'
    OR message_id LIKE 'DEMO-MSG-%';
DELETE FROM customer_service.conversations
 WHERE conversation_id LIKE 'DEMO-CONV-%';

-- 1. 会话 ×8（时间散布近 7 天，评分覆盖 1-5 星）
INSERT INTO customer_service.conversations
    (conversation_id, user_id, tenant_id, conversation_status, handling_mode,
     channel, priority, rating, rating_comment, rated_at,
     created_at, updated_at, closed_at, first_reply_at, last_activity_at)
VALUES
  ('DEMO-CONV-001', '99001', 'default', 'resolved', 'ai',   'web', 'medium',
   5, '回答很快',           now() - interval '6 days 20 hours',
   now() - interval '6 days 23 hours', now() - interval '6 days 20 hours', now() - interval '6 days 20 hours',
   now() - interval '6 days 23 hours' + interval '25 seconds', now() - interval '6 days 20 hours'),
  ('DEMO-CONV-002', '99001', 'default', 'resolved', 'ai',   'web', 'medium',
   4, NULL,                 now() - interval '5 days 22 hours',
   now() - interval '6 days 1 hour',   now() - interval '5 days 22 hours', now() - interval '5 days 22 hours',
   now() - interval '6 days 1 hour'   + interval '40 seconds', now() - interval '5 days 22 hours'),
  ('DEMO-CONV-003', '99001', 'default', 'resolved', 'ai',   'web', 'low',
   2, '没有解决问题',       now() - interval '5 days 10 hours',
   now() - interval '5 days 11 hours', now() - interval '5 days 10 hours', now() - interval '5 days 10 hours',
   now() - interval '5 days 11 hours' + interval '30 seconds', now() - interval '5 days 10 hours'),
  ('DEMO-CONV-004', '99001', 'default', 'resolved', 'human', 'web', 'high',
   5, '人工客服很专业',     now() - interval '4 days 21 hours',
   now() - interval '4 days 22 hours', now() - interval '4 days 21 hours', now() - interval '4 days 21 hours',
   now() - interval '4 days 22 hours' + interval '35 seconds', now() - interval '4 days 21 hours'),
  ('DEMO-CONV-005', '99001', 'default', 'resolved', 'ai',   'web', 'medium',
   3, NULL,                 now() - interval '3 days 19 hours',
   now() - interval '3 days 20 hours', now() - interval '3 days 19 hours', now() - interval '3 days 19 hours',
   now() - interval '3 days 20 hours' + interval '50 seconds', now() - interval '3 days 19 hours'),
  ('DEMO-CONV-006', '99001', 'default', 'resolved', 'human', 'web', 'high',
   1, '等了很久才有人',     now() - interval '2 days 20 hours',
   now() - interval '2 days 22 hours', now() - interval '2 days 20 hours', now() - interval '2 days 20 hours',
   now() - interval '2 days 22 hours' + interval '95 seconds', now() - interval '2 days 20 hours'),
  ('DEMO-CONV-007', '99001', 'default', 'resolved', 'ai',   'web', 'medium',
   4, NULL,                 now() - interval '1 days 21 hours',
   now() - interval '1 days 23 hours', now() - interval '1 days 21 hours', now() - interval '1 days 21 hours',
   now() - interval '1 days 23 hours' + interval '20 seconds', now() - interval '1 days 21 hours'),
  ('DEMO-CONV-008', '99001', 'default', 'open',   'ai',   'web', 'medium',
   NULL, NULL, now() - interval '5 hours',
   now() - interval '5 hours', now() - interval '4 hours', NULL,
   now() - interval '5 hours' + interval '30 seconds', now() - interval '4 hours');

-- 人工会话绑定演示坐席（喂坐席维度质量统计）
UPDATE customer_service.conversations
   SET assigned_agent_id = 'cs_wire_e2e'
 WHERE conversation_id IN ('DEMO-CONV-004', 'DEMO-CONV-006');

-- 2b. 演示 handoff（closed 态，喂质检日报坐席维度：承接量/满意度）
DELETE FROM customer_service.handoffs WHERE handoff_id LIKE 'DEMO-HANDOFF-%';
INSERT INTO customer_service.handoffs
    (handoff_id, conversation_id, user_id, tenant_id, handoff_state,
     trigger_type, trigger_reason, ticket_id, assigned_agent_id,
     created_at, updated_at, closed_at)
VALUES
  ('DEMO-HANDOFF-01', 'DEMO-CONV-004', '99001', 'default', 'closed',
   'complaint_escalation', '投诉升级: severity=high', 'HANDOFF-DEMO01',
   'cs_wire_e2e',
   now() - interval '4 days 20 hours', now() - interval '4 days 19 hours',
   now() - interval '4 days 19 hours'),
  ('DEMO-HANDOFF-02', 'DEMO-CONV-006', '99001', 'default', 'closed',
   'user_request', '用户主动转人工', 'HANDOFF-DEMO02',
   'cs_wire_e2e',
   now() - interval '2 days 20 hours', now() - interval '2 days 18 hours',
   now() - interval '2 days 18 hours');

-- 2. 消息（user 消息带 intent_name，喂意图 Top10；assistant 成对）
INSERT INTO customer_service.messages
    (message_id, conversation_id, role, sender_type, content,
     content_type, intent_domain, intent_name, created_at)
VALUES
  -- 001: 保修（knowledge）×2 轮
  ('DEMO-MSG-0101', 'DEMO-CONV-001', 'user',      'user',      '耳机保修多久？',            'text', 'knowledge', 'k_warranty',   now() - interval '6 days 23 hours'),
  ('DEMO-MSG-0102', 'DEMO-CONV-001', 'assistant', 'assistant', '蓝牙降噪耳机 Pro 为 12 个月质保…', 'text', 'knowledge', 'k_warranty', now() - interval '6 days 22 hours 59 minutes'),
  ('DEMO-MSG-0103', 'DEMO-CONV-001', 'user',      'user',      '发票怎么开？',              'text', 'knowledge', 'k_faq',        now() - interval '6 days 22 hours'),
  ('DEMO-MSG-0104', 'DEMO-CONV-001', 'assistant', 'assistant', '提供抬头与税号后 1 个工作日内开具…', 'text', 'knowledge', 'k_faq', now() - interval '6 days 21 hours 59 minutes'),
  -- 002: 订单查询 ×3 轮
  ('DEMO-MSG-0201', 'DEMO-CONV-002', 'user',      'user',      '查我的订单',                'text', 'transaction', 't_order_status', now() - interval '6 days 1 hour'),
  ('DEMO-MSG-0202', 'DEMO-CONV-002', 'assistant', 'assistant', '## 您的订单（共 15 条）…',  'text', 'transaction', 't_order_status', now() - interval '6 days 59 minutes'),
  ('DEMO-MSG-0203', 'DEMO-CONV-002', 'user',      'user',      'DEMO-1002 到哪了',          'text', 'transaction', 't_order_status', now() - interval '6 days 30 minutes'),
  ('DEMO-MSG-0204', 'DEMO-CONV-002', 'assistant', 'assistant', 'DEMO-1002 已签收…',         'text', 'transaction', 't_order_status', now() - interval '6 days 29 minutes'),
  ('DEMO-MSG-0205', 'DEMO-CONV-002', 'user',      'user',      '那它的物流轨迹呢',          'text', 'transaction', 't_logistics',    now() - interval '6 days'),
  ('DEMO-MSG-0206', 'DEMO-CONV-002', 'assistant', 'assistant', '轨迹：已发货 → 运输中…',    'text', 'transaction', 't_logistics',    now() - interval '5 days 23 hours 59 minutes'),
  -- 003: 物流催件 ×2 轮
  ('DEMO-MSG-0301', 'DEMO-CONV-003', 'user',      'user',      '快递一直不动怎么办',        'text', 'transaction', 't_logistics',    now() - interval '5 days 11 hours'),
  ('DEMO-MSG-0302', 'DEMO-CONV-003', 'assistant', 'assistant', '已为您发起催件，24 小时内更新…', 'text', 'transaction', 't_logistics', now() - interval '5 days 10 hours 59 minutes'),
  ('DEMO-MSG-0303', 'DEMO-CONV-003', 'user',      'user',      '配送范围有哪些',            'text', 'knowledge',   'k_faq',          now() - interval '5 days 10 hours'),
  ('DEMO-MSG-0304', 'DEMO-CONV-003', 'assistant', 'assistant', '国内 48 小时内发货…',       'text', 'knowledge',   'k_faq',          now() - interval '5 days 9 hours 59 minutes'),
  -- 004: 投诉 → 转人工（坐席 cs_wire_e2e 接手）
  ('DEMO-MSG-0401', 'DEMO-CONV-004', 'user',      'user',      '我要投诉，商品破损了',      'text', 'complaint',  'c_complaint',    now() - interval '4 days 22 hours'),
  ('DEMO-MSG-0402', 'DEMO-CONV-004', 'assistant', 'assistant', '已为您创建投诉工单并转接人工…', 'text', 'complaint', 'c_complaint', now() - interval '4 days 21 hours 59 minutes'),
  ('DEMO-MSG-0403', 'DEMO-CONV-004', 'user',      'user',      '好的，麻烦尽快',            'text', 'complaint',  'c_complaint',    now() - interval '4 days 21 hours 30 minutes'),
  ('DEMO-MSG-0404', 'DEMO-CONV-004', 'assistant', 'assistant', '您好，我是您的专属客服，破损商品可全额退款…', 'text', 'complaint', NULL, now() - interval '4 days 21 hours'),
  -- 005: 商品咨询 ×2 轮
  ('DEMO-MSG-0501', 'DEMO-CONV-005', 'user',      'user',      '榨汁杯可以打冰块吗',        'text', 'product',    'k_product',      now() - interval '3 days 20 hours'),
  ('DEMO-MSG-0502', 'DEMO-CONV-005', 'assistant', 'assistant', '可以打少量小块冰，需加水…', 'text', 'product',    'k_product',      now() - interval '3 days 19 hours 59 minutes'),
  ('DEMO-MSG-0503', 'DEMO-CONV-005', 'user',      'user',      '音箱防水等级多少',          'text', 'product',    'k_product',      now() - interval '3 days 19 hours'),
  ('DEMO-MSG-0504', 'DEMO-CONV-005', 'assistant', 'assistant', 'IPX7，可短时浸泡…',         'text', 'product',    'k_product',      now() - interval '3 days 18 hours 59 minutes'),
  -- 006: 退款 → 人工（不满意评分 1 星）
  ('DEMO-MSG-0601', 'DEMO-CONV-006', 'user',      'user',      '我要退款',                  'text', 'after_sales', 'as_refund',      now() - interval '2 days 22 hours'),
  ('DEMO-MSG-0602', 'DEMO-CONV-006', 'assistant', 'assistant', '请提供订单号与退款原因…',   'text', 'after_sales', 'as_refund',      now() - interval '2 days 21 hours 59 minutes'),
  ('DEMO-MSG-0603', 'DEMO-CONV-006', 'user',      'user',      '转人工，AI 说不清楚',       'text', 'human',      'h_handoff',      now() - interval '2 days 21 hours'),
  ('DEMO-MSG-0604', 'DEMO-CONV-006', 'assistant', 'assistant', '已为您转接人工客服…',       'text', 'human',      'h_handoff',      now() - interval '2 days 20 hours 59 minutes'),
  -- 007: 保修/售后混合
  ('DEMO-MSG-0701', 'DEMO-CONV-007', 'user',      'user',      '耳机充不进电怎么办',        'text', 'after_sales', 'as_repair',      now() - interval '1 days 23 hours'),
  ('DEMO-MSG-0702', 'DEMO-CONV-007', 'assistant', 'assistant', '12 个月质保内可换新…',      'text', 'after_sales', 'as_repair',      now() - interval '1 days 22 hours 59 minutes'),
  ('DEMO-MSG-0703', 'DEMO-CONV-007', 'user',      'user',      '换货流程是什么',            'text', 'after_sales', 'as_return',      now() - interval '1 days 22 hours'),
  ('DEMO-MSG-0704', 'DEMO-CONV-007', 'assistant', 'assistant', '15 天内同款换新，质量问题运费商家承担…', 'text', 'after_sales', 'as_return', now() - interval '1 days 21 hours 59 minutes'),
  -- 008: 未关闭会话（正在进行的通用问答）
  ('DEMO-MSG-0801', 'DEMO-CONV-008', 'user',      'user',      '会员有什么权益',            'text', 'knowledge',  'k_faq',          now() - interval '5 hours'),
  ('DEMO-MSG-0802', 'DEMO-CONV-008', 'assistant', 'assistant', '会员权益包括…',             'text', 'knowledge',  'k_faq',          now() - interval '4 hours 59 minutes');

COMMIT;
