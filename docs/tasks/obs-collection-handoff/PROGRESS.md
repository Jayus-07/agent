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

- [x] `_trace_dto.py`：`to_trace_dto`/`stored_dict_to_dto` 透出 `workflow_kind`
- [x] `runKind.ts`：优先消费 DTO `workflow_kind`（rag_query/knowledge_index 直接判定），
      workflow_name 映射保留为存量兜底；新增 runKind.test.ts 6 用例
- [x] `/traces/stats` 增加 `by_domain`（total/errors，classify_trace_source 单一事实源，
      含 SOURCE_UNKNOWN「其他」桶）
- [x] `OverviewPanel.tsx` 业务域分布切服务端口径，删除样本聚合标注；
      `TRACE_SOURCE_LABELS` 补空串「其他」桶对齐
- [x] `dev-svc.bat`：修注释漂移（admin/web 已是容器化镜像非 next dev）+
      新增 `rebuild_frontend`（build + up -d frontend-admin frontend + 健康等待）

验证证据（2026-10-30）：
- 前端：vitest 全量 **576 passed (83 files)**；`tsc --noEmit` **rc=0**
  （OverviewPanel.test.tsx 重写 6 用例：by_domain 服务端口径/运行中卡 Link/模型健康卡
  三态/最近错误/失败态；runKind.test.ts 新增）
- 后端定向：`tests/test_trace_fixes.py tests/api/test_observability_tool_filter.py
  tests/api/test_observability_trace_children.py tests/api/test_observability_trace_paging.py
  tests/observability/test_trace_source.py tests/observability/test_trace_error_derivation.py`
  → **57 passed, 2 failed**；2 failed
  （test_observability_trace_children 的 get_trace 签名不匹配）经主仓库 main 对照
  **同样失败**，为其他会话 authz 改动的既有失败，非本任务引入，不改动。

## P3 实施记录

- [x] 总览运行中卡改 Link 跳 `status=running`（"授权活跃端点实时计数"→"点击进入运行中筛选"）
- [x] 模型健康卡接 `GET /sys/model-health`（复用既有探测缓存端点 + 前端既有
      listModelHealth/healthStatusLabel，无新造假端点；读取失败如实显示"未采集"）
- [ ] 保真度差异清单逐项核对（P4 前回填下表）

| 差异项 | 结论 |
| --- | --- |
| 总览缺模型健康卡 | 已对齐（/sys/model-health 探测缓存，beat 周期，本页不在线探测） |
| 运行中卡不可点击 | 已对齐（Link → status=running 筛选） |
| HTTP 拆分依赖 span.http_breakdown | 有意偏离：主图无该采集维度，前端空值如实显示"未采集"，不在本任务伪造数据 |
| compare 对比页旧样式 | 有意偏离：非本任务范围，待对比页重构任务承接 |
| 列表 DTO 未下发 workflow_kind | 已对齐（P2 透出，本行从"后端契约缺口"披露块移除） |

## P4 验证记录

### 部署（worktree → 主栈，COMPOSE_PROJECT_NAME=agent）

- 前端：`compose build frontend-admin frontend` + `up -d` → agent-frontend-admin-1 /
  agent-frontend-1 healthy；容器内构建产物含「服务端统计口径」（新代码版本锚点）
- 后端：`compose build app 等 9 服务` + `up -d` → app/worker/beat healthy；
  容器内验证 by_domain(3)/workflow_kind(3)/record_llm_call_span(4)/llm_call_span.py 存在
- dev-svc.bat 实测：worktree 内 `COMPOSE_PROJECT_NAME=agent dev-svc.bat rebuild /y`
  → backend rebuild + **frontend rebuild（新增 rebuild_frontend 动作）** 全部执行，
  两前端健康检查 ready，最终 status 全 UP
- ⚠️ 发现并记录的坑（不属本任务修复范围）：dev-svc.bat 未设置 COMPOSE_PROJECT_NAME，
  在非 `agent` 目录名的 worktree 里直接跑会构建出平行项目名的镜像/容器——
  本次以 COMPOSE_PROJECT_NAME=agent 环境变量规避；主仓库目录名=agent 不受影响。

### 浏览器实测（admin :3200，obs_handoff_viewer/admin 测试账号）

| 页面 | 结果 | DOM 证据 |
| --- | --- | --- |
| 总览 tab=overview | PASS | 「业务域分布（近 24 小时 · 服务端统计口径）」+ 数据行「其他 25 条，错误 10」；运行中卡为 Link（href=...status=running）；「模型健康」卡：18 个已探测模型 · 10 健康 · 8 异常，列出 deepseek-v4-flash 异常行；终态分布 9 项全部 Link |
| 执行记录 tab=traces | PASS | 列表渲染错误筛选结果；运行对象徽标（工作流/平台工作流）按 runKind 渲染 |
| 详情页 /traces/{id} | PASS | 四问布局（①错在哪 ②慢在哪·Span 甘特 ③答了什么）；存量 trace 空值如实显示「本次 Trace 未记录 LLM 调用」「错误码：未采集」 |
| 网关安全 tab=gateway | PASS | Prometheus 未启动（profile 储备）时显式降级文案，不伪造空统计 |
| Token 用量 tab=tokens | PASS | 按组件×模型用量表 + 调用明细入口 |

### API 硬验证（带授权 token）

- `GET /api/observability/traces?limit=5` → 每行含 `workflow_kind`
  （main=langgraph_workflow；daily_report/rag_agent/inventory_alert=other）
- `GET /api/observability/traces/stats?hours=24` → `by_domain`：
  `{"": {total:25, errors:10}, ai_assistant: 0, cs: 0, travel: 0}`——与总览页渲染一致

### P1 实机闭环验证（触发真实寒暄请求）

- `POST /api/chat/agent`（网关链路，走 general_chat 直连主 LLM）→ 200
- 新 trace（7bf629876f5a）spans = `["workflow","workflow","workflow","retrieval",
  "retrieval","embedding","retrieval","tool_call","tool_call","llm_call","agent"]`
  ——**llm_call span 出现**（修复前 28 条 agent trace 恒为 0）
- span 指标：model_name=doubao-seed-2.0-mini，prompt_tokens=169，
  total_tokens=221，cost_cny=0.000138，duration_ms_call=1317.1，
  token_source=proxy_span；input.prompt/response 输出快照齐全
- 一个成功调用 = 1 行 llm_usage + 1 个 llm_call span（不双计）
- prompt_versions：`metadata.prompt_versions["general_chat.system"]=3`（精确记录生效）

### 实测中发现并修复的缺陷（第三提交）

- `record_prompt_version` 与 runner 的 `{key: version}` map 快照冲突：
  旧实现按 list 迭代 dict（str.get 抛 AttributeError 被静默吞掉），
  workflow trace 上 render_sync 的精确版本记录从未生效——修复为兼容
  map（原地更新）/list（同 key 更新、新 key 追加）两种形态，补回归测试。

### 最终测试汇总

- frontend-admin：vitest 全量 **576 passed (83 files)**，`tsc --noEmit` **rc=0**
- backend 定向：P1 新增 26 passed（含 map 兼容回归）+ P2 相关 57 passed
  （2 failed 为 main 既有失败，主仓库对照确认）；proxy/tracer 既有回归 33 passed
- 测试账号清理说明：`auth.users` 新增 obs_handoff_viewer（admin，tenant default）
  为本任务实测专用，PR 合并后可删除或由运维保留

