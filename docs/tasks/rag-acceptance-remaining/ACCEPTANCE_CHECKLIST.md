# RAG 验收剩余门禁

本清单只跟踪当前尚未关闭的生产验收门禁。稳定行为契约见 [RAG 域文档](../../domains/rag.md)，执行步骤见 [收口方案](IMPLEMENTATION_PLAN.md)，当前结果见 [PROGRESS.md](PROGRESS.md)。代码或部署变化影响已验收范围时，应重新打开对应门禁。

## 上传与索引指标（O11）

- [ ] 在目标环境确认 app、RAG 服务与 index worker 的版本、启动时间和指标出口，确保查询进程与真实请求路径一致。
- [ ] 经真实上传成功、失败路径核对 `rag_upload_total` 的 `status` 标签样本；成功与失败都必须出现，duplicate 按独立状态观察。
- [ ] 核对 `rag_index_duration_seconds` 的终态样本，以及 `task_queue_wait_seconds` 的 worker 排队样本；指标须能从承载对应请求的目标环境出口查询。
- [ ] 若需改埋点，补充针对性回归；部署后记录查询环境、版本、时间窗口和结果。

## 延迟观察窗口

- [ ] 确认用户入口、部署版本、Trace 查询和 SLA 看板指向同一目标环境。
- [ ] 按 [SLO 定义](../../observability/slo.md) 完成预定的连续观察窗口，不能用单次成功请求替代。
- [ ] 窗口内核对请求结果、端到端耗时、未归因耗时、引用，以及 SLA 汇总、错误率和无证据率。
- [ ] 单独记录窗口起止时间和新采集的 Trace；窗口外历史数据不计入本轮样本，也不得删除或改写。

## 生产就绪判定

关闭上述开放项不自动代表生产就绪。所有受影响的既有门禁仍须有效：

```text
RAG_UPLOAD_GATE_PASS = AUTH_PASS AND IDEMPOTENCY_PASS AND VERSION_CONSISTENCY_PASS AND INDEX_ATOMICITY_PASS AND FAILURE_RECOVERY_PASS
RAG_RETRIEVAL_GATE_PASS = SUBJECT_FAIL_CLOSED_PASS AND PERMISSION_ISOLATION_PASS AND HYBRID_RETRIEVAL_PASS AND RERANK_PASS AND EVIDENCE_GATE_PASS AND CITATION_PASS
RAG_QUALITY_GATE_PASS = RETRIEVAL_EVAL_PASS AND NO_ANSWER_PASS AND ZERO_CROSS_TENANT_RECALL AND FAITHFULNESS_PASS
RAG_OBSERVABILITY_GATE_PASS = TRACE_PASS AND LATENCY_METRICS_PASS AND ERROR_CODE_PASS AND MODEL_VERSION_TRACE_PASS
RAG_PRODUCTION_READY = RAG_UPLOAD_GATE_PASS AND RAG_RETRIEVAL_GATE_PASS AND RAG_QUALITY_GATE_PASS AND RAG_OBSERVABILITY_GATE_PASS
```

只有目标环境的当前证据满足全部门禁时，才可声明 `RAG_PRODUCTION_READY=true`。生产数据写操作、索引重建、共享容器重启或发布状态变更，按获批的变更窗口执行。
