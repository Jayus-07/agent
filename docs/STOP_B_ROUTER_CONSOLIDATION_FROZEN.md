# STOP B Router Consolidation 冻结记录

冻结日期：2026-09-29
冻结基线：`6ed42b780c174dc67b0a1b5a9ef2ba85491a1a75`

```ini
STOP_B_ROUTER_CONSOLIDATION_FROZEN=true
ROUTER_NODE_ID_CHANGED=false
STATE_CONTRACT_CHANGED=false
SSE_CHANGED=false
CHECKPOINT_CHANGED=false
LEGACY_FALLBACK_AVAILABLE=true
```

## 冻结范围

- `router_node` 仍是主图唯一 Router 入口，旧 `route_mode` 与 `route_selector` 映射保持不变。
- `DomainDecision`、`CapabilityDecision`、`ExecutionModeDecision` 作为旁路决策快照进入 state。
- `route_decision`、`route_mode`、`query_understanding` 仍是兼容字段；新增快照不覆盖其既有语义。
- legacy rule → vector → LLM Router、hierarchical Router、客服/旅游/选品 prefilter 与 `TaskRouter` 均保留。
- Evaluation Runtime 只读已有 RAG snapshot；Index Pipeline 与 evaluation/runtime 生命周期已隔离。

## STOP C 使用的冻结事实

STOP C 只增加连续会话测试、冻结记录和验收报告，不修改上述生产 Router、LangGraph 节点、SSE、checkpoint、前端或 RAG retrieval 契约。
