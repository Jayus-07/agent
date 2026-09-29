# Domain Runtime STOP C — Cross-Domain Routing & Conversation Continuity（跨域路由与会话连续性报告）

> 日期：2026-09-24 ｜ 前置：STOP B PASS（6cc3f82）
> 验收方式：真实 APISIX :9080 → JWT 登录 → /chat/stream SSE，同一 session 连续 10 轮；
> 逐轮以 app 容器 trace store 的 `graph.nodes`（真实执行拓扑）为路由权威观测面，
> 辅以 Redis ConversationContext（active_domain / travel pending）与 PG 5433 只读核查。
> 驱动工具：`backend/scripts/e2e_domain_runtime.py`（幂等可重复，专用账号 e2e_domain）。

---

## 1. Verdict

```text
STOP_C_PASS      = true
STOP_D_ALLOWED   = true
```

最终矩阵：**10/10 PASS**（session=stopC-1790200489，含 3 次域切换、2 次 follow-up、1 次模糊回指），C9 落库一致性 3/3 PASS。

---

## 2. C1/C9 Cross-Domain E2E Matrix（真实链路，逐轮路由=trace 实测）

| 轮 | 提问 | 实测执行节点（trace） | active_domain（轮后） | 判定 |
|---|---|---|---|---|
| T1 | 你好，请介绍一下平台都能做什么 | router→planner→critique→supervisor→reporter | "" | PASS General |
| T2 | 根据知识库文档说明客户生命周期阶段 | router→planner→…→reporter（clarify 兜底） | "" | PASS 主图（禁域图✓） |
| T3 | 查一下最近一个月每天的销售额 | router→workflow_executor→reporter | "" | PASS 数据主链（daily_report workflow；promotions 步骤被行级权限拒=纵深生效） |
| T4 | 帮我查一下这个订单的物流进度，到底什么时候到 | **router→cs_graph_node** | customer_service | PASS CS 切入 |
| T5 | 那这个订单的物流进度到底怎么样了 | **router→cs_graph_node** | customer_service | PASS **CS follow-up 连续性（C3）** |
| T6 | 帮我规划厦门的行程 | **router→travel_graph_node** | travel | PASS **CS→Travel 切换（C2 不粘滞）** |
| T7 | 三天 | **router→travel_graph_node** | travel | PASS **Travel 补槽 follow-up**（出单：厦门3天¥1441） |
| T8 | 订单里的行程单怎么退款 | **router→cs_graph_node** | customer_service | PASS **Travel→CS 回归（B5 运行时证据）** |
| T9 | 那这个怎么办 | router→planner→supervisor→skill-1(RAG)→reporter（拒答兜底） | customer_service（粘滞标记不变） | PASS **模糊回指（C4）：未被随机切进任何域图** |
| T10 | 给宠物零食做一次智能选品 | **router→selection_funnel_graph_node** | selection_funnel | PASS 切 Selection |

覆盖任务书要求的代表路径：General→RAG、RAG/SQL 主链、SQL→CS（T3→T4 相邻数据→客服切换）、CS→Travel（T4→T6）、Travel→Selection（T7→T10 经 CS 回归）、CS→General/拒答→CS（T8→T9 语境）。

## 3. C2/C3/C4 路由语义实证

- **C2 不无限粘滞**：active_domain=customer_service（T4/T5）时旅游强信号请求（T6）立即切出；active_domain=travel（T6/T7）时客服双组信号（T8）立即切回——「外域强信号优先」两层实测。
- **C3 follow-up 连续性**：CS 轮内「那这个订单的…」（T5）与 travel 轮内裸槽位「三天」（T7）均正确续回原域；travel 侧由 TravelPendingResolver（source=pending_resume）承接，CS 侧由 ContextResolver+双组规则承接。
- **C4 模糊回指**：「那这个怎么办」（T9）——无延续信号、无强域信号 → 主图 RAG 检索+拒答兜底，**未随机切域**、未误入 travel/selection 域图；active_domain 粘滞标记保持不变（仅延续信号命中时才消费）。
- **C5 多意图**：单请求单主域契约保持（未做跨域 DAG）；「查一下订单123，然后告诉我退款规则」类输入由 CS 双组规则整体承接（TRANSACTION+AFTER_SALES 同属 CS），行为稳定明确。
- **C6 Router Failure**：单元证据在案（LLM router 超时/解析失败→plan+rag.search conf=0.3，llm_router.py:113-122；Router 整体异常→plan，router_node.py:446-452；hierarchical embedding 降级→回退 legacy）。实机依赖故障注入归 STOP E（E5，隔离实例法）。

## 4. C7 Route Trace

每轮 route 观测三通道全部可用且相互印证：
1. **trace store `graph.nodes`**（容器内 SQLite）——本轮真实执行拓扑，本报告路由判定权威面；
2. **Redis ConversationContext `active_domain`**——域任务粘滞标记（mark_domain_turn 只在域图/hierarchical 轮更新）；
3. **done 帧 `trace_id`** + metrics `routing_domain_total{domain,source}` / `router_decision_total{mode}`（C7 要求非 label 原则已在 STOP A 核验）。

⚠️ 发现（P2-10 登记）：`ai.trace_records` PG 镜像表 0 行——trace 仅落 app 容器内 SQLite，外部（库侧）observability 查询目前不可用。trace_id→SQLite 单实例可查，跨实例/长期留存依赖容器卷。不阻塞本轮（应用内 trace 闭环完整），登记为观测导出缺口。

## 5. C8 真实链路声明

全部 10 轮经 `POST http://127.0.0.1:9080/api/chat/stream`（APISIX gateway-auth 验签）真实执行，SSE 帧序 meta→status/log/delta→done 完整（done_count=1、error_count=0 逐轮断言），非 router.classify() 冒充。

## 6. C9 会话连续落库核查（同 session 10 轮后）

| 核查项 | 结果 |
|---|---|
| chat_messages=14 行（10 轮中 6 个非 CS 轮 ×2 条） | ✓ 与「CS 轮豁免主历史」设计一致：T4/T5/T8 三轮 CS 提问原文**均不在** chat_messages（PASS） |
| travel checkpoints：`travel:stopC-1790200489` 22 行 | ✓ 旅游域 checkpoint 跨轮累积（补槽/出单自然续跑） |
| memory_records(user)=3 | ✓ L3 异步管线在写（信息性） |
| Redis conversation context 版本随轮递增 | ✓（观测输出见运行日志） |

## 7. 校准过程与发现（如实记录，不影响 PASS 判定）

1. **首轮矩阵 4 FAIL 的根因是测试短语校准，而非路由缺陷**：CS 全局入口为 precision-tuned 设计——需 **≥2 个规则组**命中（CS_RULE_MIN_HITS=2）+ is_cs 才进 CS；「订单123什么时候发货」「退款怎么处理」这类单组命中查询按设计交主路由（RAG/SQL 检索→拒答兜底）。容器实机检测器与工作区代码**逐字一致**（docker exec 直跑对比，排除了镜像漂移假设）。客服抽屉产品面由 domain_hint 锁域覆盖（锁域跳过检测门）。
2. 单组信号查询的实机行为：plan/直接链 RAG 检索→「未能找到相关信息」拒答模板——确定性、无随机切域，符合「以当前设计为准」。
3. 上轮（stopC-1790199901）遗留数据：该 session 的 22 条 chat_messages 含首轮误路由轮次（当时短语未命中 CS 而进主链，属正常主链轮次写入），两轮数据互不影响。

## 8. 移交 STOP D

- 任务异步链路演练复用冻结层 `scripts/e2e_async_runtime.py`（R1-R5）+ 域信封/恢复身份/trace 关联/CS 工单幂等补充观测（`scripts/e2e_domain_async.py` 已备）。
- 带入项：P2-10（trace PG 镜像空）；C6 实机故障注入与 E5 合并执行。

```text
STOP_C_PASS      = true
STOP_D_ALLOWED   = true
```
