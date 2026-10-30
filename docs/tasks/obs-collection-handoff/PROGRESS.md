# PROGRESS: obs-collection-handoff

## 2026-10-30 开工

- worktree `D:/Program Files/workplace/agent-wt-obs-handoff` 自 main@2517e98 创建，branch `feat/obs-collection-handoff`。
- 代码实查结论（P1 设计依据）：
  - 所有成功 LLM 调用（`_BoundLLMProxy.invoke/ainvoke`、`_LLMProxy` 全部包装方法、stream/astream、
    `record_llm_result`、`__call__`）都汇聚到 `proxy._record_tokens` 单点 → 选它做统一 span 出口。
  - 双计防线：一个成功调用 = 恰一行 llm_usage + 恰一个 llm_call span；trace.usage 主口径由
    finish() `_backfill_usage_from_store` 按 llm_usage 聚合覆盖 `_aggregate_usage` 的 span 求和；
    调用方已管理 LLM span（rag llm_generate / tool_selector_llm / sql_agent 阶段 span /
    self_correction_rewrite / reranker，判据=最内层未收口 span 的 kind=llm 或 type=llm_call）时跳过创建。
  - 脱敏：span 文本走既有 DTO 层 redaction（masked/summary 级 `redact_trace_dto` 对
    `span.llm_call.prompt_text/response_text` 打码/剥离，input/output 同样覆盖），
    与 chain.py llm_generate 现行口径一致（写入截断文本，存储保真、读层脱敏）。
- prompt_versions 缺口定位：
  - daily_report → `InventoryAnalyzer` 用 `get_template_sync` 不记版本 → 补 `record_template_usage_sync`。
  - inventory_alert → 全链无 LLM 调用（SQL+规则+邮件），prompt_versions=0 属如实空值，不伪造。
  - rag/chain.py 已有 `_record_runtime_prompt_usage()`，DB 19/37 缺口为该机制（2026-10-01 落地）
    之前的存量 trace；不重复加埋点。

## P1 实施记录

- [x] 新增 `backend/observability/llm_call_span.py`：统一 llm_call span 出口
- [x] `backend/infra/llm/proxy.py` 接线（prompt 快照 + span 出口调用，软失败）
- [x] `InventoryAnalyzer.run` 补 `record_template_usage_sync("capability.inventory_analyzer")`
- [x] 定向测试：span 出现/去重/脱敏矩阵/不双计 + inventory_analyzer 版本记录

验证证据（2026-10-30）：
- `PGPORT=5433 python -m pytest tests/observability/test_llm_call_span.py
  tests/observability/test_prompt_trace_coverage.py tests/observability/test_prompt_version_trace.py
  -q --no-cov` → **25 passed**（新增 19：span 出现/时间窗回写/两类 dedup/prompt 快照形态/
  _record_tokens 集成/无 usage 不出 span/不双计/脱敏矩阵 masked+summary+full）
- 回归：`tests/test_llm_span_fields.py tests/test_llm_stream_sink.py
  tests/observability/test_trace_token_provenance.py tests/observability/test_llm_usage_component.py`
  → **33 passed**（首次运行 21 ERROR 为 worktree 缺 .env 的 PG 连接问题，拷贝后全绿）
- 有意偏离说明：保留调用方既有 LLM span（RAG llm_generate 携带 question/completion_text
  更丰富语义），proxy 出口负责无 span 路径并以 kind=llm/type=llm_call 判据去重——
  "替代散点"落为"后续新调用点无需再散点埋点"，不回删存量高价值埋点。

## P2 实施记录

- [ ] `_trace_dto.py`：`to_trace_dto`/`stored_dict_to_dto` 透出 `workflow_kind`
- [ ] `runKind.ts`：优先消费 DTO `workflow_kind`，workflow_name 映射保留为兜底；测试更新
- [ ] `/traces/stats` 增加 domain 维度（复用 classify_trace_source 单一事实源）
- [ ] `OverviewPanel.tsx` 业务域分布切服务端口径，删除样本聚合标注
- [ ] `dev-svc.bat`：修注释漂移 + rebuild 增加 admin/web；实测一次生效

## P3 实施记录

- [ ] 总览运行中卡改 Link 跳 `status=running`
- [ ] 模型健康卡接 llm_model_health（复用既有 health API，无新造假端点）
- [ ] 保真度差异清单逐项核对（结论表在完成后回填）

## P4 验证记录

- [ ] vitest 全绿 + tsc --noEmit 通过（frontend-admin）
- [ ] 后端定向 pytest 全绿
- [ ] compose 重建 frontend-admin + backend，浏览器实测四页与详情
- [ ] PR 合并 main（描述引用根因证据）
