# STOP B Trace Metadata Audit

日期：2026-09-29
范围：STOP B/C 收口缺口 1 的只读审计；未修改 Router 行为、LangGraph 图、SSE 或 checkpoint。

## 1. 结论

现有 `router_node` 已把 `domain_decision`、`capability_decision` 与
`execution_decision` 写入 LangGraph state，但没有写入当前 `TraceRecord.metadata`。
因此，现有测试证明了 state/checkpoint 可序列化，不能证明持久化 trace 可查询
Router 决策。修复应复用当前 trace context 与 `TraceRecord.metadata`，不新增存储或
trace 生命周期。

## 2. Trace metadata 在哪里创建

- `backend/observability/tracer.py::TraceRecord` 以 `metadata: dict = field(default_factory=dict)`
  创建 metadata 容器。
- `backend/orchestration/graph/runner.py` 在图运行前调用
  `trace_collector.start(question, session_id, workflow_name="agent")` 创建并绑定本轮
  `TraceRecord`。
- `TraceCollector.start()` 创建 `id` 与 `request_id`，通过 `ContextVar` 绑定 current trace；
  图 worker 通过 `trace_collector.bind(trace)` 继承这一上下文。

## 3. Trace metadata 在哪里更新与持久化

- 现有业务节点直接更新 current trace，例如客服节点写
  `trace.metadata["cs_handoff_triggers"]`，旅游节点写 `travel_validation`、
  `travel_quality` 等 metadata。
- `TraceCollector.finish()` 在收尾时补充 prompt 版本、聚合 usage，并将完整 record
  交给现有 `trace_writer` 异步队列。
- `backend/observability/trace_store_pg.py::PostgresTraceStore.save_dict()` 将完整 record
  （包括 `metadata`）序列化为 JSON，写入既有 PostgreSQL trace 表。

## 4. router_node 如何访问当前 trace context

`backend/orchestration/graph/router_node.py` 已在 continuation、客服理解和 CS redirect
等路径中使用 `trace_collector.current()` 写 tags。这表明 Router 节点处于与
GraphRunner 相同的 current-trace ContextVar 中，可安全使用同一机制写 metadata；无需
传递 trace_id、创建新的 collector 或修改节点 ID。

## 5. LLM / Tool span 与 trace_id 的关联

- GraphRunner 创建 root trace，节点/Skill/Tool 用 `trace_collector.start_span()` 在该
  current trace 上创建 span。
- `backend/observability/llm_usage_store.py::current_usage_attribution()` 从 current trace
  读取 `trace_id`、`request_id`、`session_id`；LLM proxy 将这些字段随 usage 明细写入。
- `TraceCollector.finish()` 以同一 `trace_id` 回填 usage，因此 Router metadata 写入当前
  record 不会改变 LLM usage、provider usage 或其关联键。

## 6. 最小修复边界

新增一个纯适配器模块，将三份已经生成的决策快照压缩为无 query、无 prompt、无候选
大对象的 `metadata["router"]`。`router_node._with_router_decisions()` 在 state 写回后调用
该适配器；无 current trace 时静默 no-op，不能影响路由、fallback 或状态结果。

建议 metadata 仅保留：

```json
{
  "router": {
    "domain": "customer_service",
    "subflow": null,
    "capability": "customer_service",
    "mode": "direct",
    "confidence": 0.93,
    "source": "hierarchical"
  }
}
```

`confidence` 取 capability confidence（无 capability 时取 domain confidence）；不记录
`reasoning`、`candidates`、原始 query 或其它可能扩大的对象。

## 7. Validation Integrity 审计

STOP B/C 新增文档为：

- `docs/architecture/2026-09-28-Architecture-Simplification-STOP_B_Pre_Implementation_Audit.md`
- `docs/architecture/2026-09-28-Architecture-Simplification-STOP_B_Router_Consolidation_Report.md`
- `docs/STOP_B_ROUTER_CONSOLIDATION_FROZEN.md`
- `docs/STOP_C_Cross_Domain_Continuity_Report.md`

对 STOP B 至 STOP C 历史范围执行 `git diff --check` 的唯一输出为预实施审计文档第
233 行的 `new blank line at EOF`。STOP C 自身提交的 diff check 无输出。

## 8. 风险与验证要求

- 适配器必须只写 `trace.metadata["router"]`，不读写 state、checkpoint 或 SSE。
- metadata 注入失败必须被吞掉并记录 debug 日志，保持 Router 旧结果不变。
- 新测试需在真实 `TraceCollector` current context 内调用 `router_node` 适配写回，并断言
  `trace.metadata["router"]`；不能只断言 state。
- 最终需重跑 Router 回归、CS/SQL/RAG、Travel evaluation 和运行时兼容测试；Travel 超时
  时必须报告 `TRAVEL_EVALUATION_VERIFIED=false`。
