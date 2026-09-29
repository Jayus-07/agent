# Chat/RAG Production Closure 验收报告（2026-09-23）

**结论：CHAT_RAG_PRODUCTION_READY = true**
用户真实链路「上传 → 索引 → 聊天窗口提问 → RAG 命中 → 带引用回答 → 多轮继续」全部实证通过，全部走 APISIX :9080 真实入口。

## A. Chat Input

| 层 | 原限制 | 现状 |
|---|---|---|
| 前端 | **无任何限制**（超长输入直达后端吃 422） | `chatInputLimit.ts` 20000 字常量；≥80% 显示计数器；超限禁发 + 提示走知识库上传（后端仍是权威） |
| API schema | `ChatRequest.question max_length=2000`（422 来源①） | `max_length=CHAT_INPUT_MAX_CHARS`（config 单一来源） |
| 服务层 | Input Guard `GUARD_MAX_INPUT_CHARS/TOKENS=2000`（422 来源②，第二道闸） | 默认值跟随 `CHAT_INPUT_*` 配置（20000/8000）；拒绝话术同步取配置值（73b4f8e） |
| /chat/stream 手动解析 | `detail=f"ChatRequest 解析失败: {e}"`（**泄漏 Pydantic 异常全文**） | 业务错误 `CHAT_INPUT_TOO_LARGE`（code=INVALID_PARAM + details.reason + limit_chars/limit_bytes），实现细节只落日志 |
| 网关 APISIX | `client_max_body_size 0`（nginx 层不限） | 不变；产品限制以 app 层为权威（CHAT_INPUT_MAX_BYTES=65536 在 /chat/stream 校验） |
| DB | `chat_messages.content TEXT` 无列限制 | 不变 |

最终值：**CHAT_INPUT_MAX_CHARS=20000 / MAX_BYTES=65536 / MAX_TOKENS=8000**（env 可覆盖，.env.example 有定位说明）。
取值依据：窗口 8192 tokens（input_budget 7168）；20000 字覆盖「较长问题/配置/代码片段/结构化需求」，纯中文极量输入由 Guard token 档拦截并引导走 `/api/rag/upload`。**产品输入限制 ≠ LLM Context 预算**，二者概念已分离并各自配置化。

## B. KB Collection Root Cause（Phase 4 的「doc_db/chroma 错位」定性是误诊）

实测澄清：
- collection 命名本身**统一且一致**：`chroma`（chunk 级）/ `doc_db`（文档级）/ `router_index`（路由索引），全部在 PG `rag_vectors` 表，kb 隔离靠 **metadata `kb_id` filter**，不存在「上传一套命名、检索另一套命名」。
- 真正根因是**主体授权判定链**：
  1. `auth.users` 全量 dept 为空 → JWT 无 dept claim → 网关不注入 `X-User-Dept`；
  2. `tools/rag.py` 与 `routes/rag*.py` 把「已登录但无部门」误判为 **customer**（宁严勿漏 fail-safe）；
  3. customer 主体只可见 audience=customer 的 cs_* 库 → **policy_* 全部被主体授权剔除**（日志实证：`主体授权剔除越界库文档 4 条 (allowed=[cs_*])`）→ 检索空 → EvidenceGate 正确拒答「知识库暂无相关资料」。
  4. 次因：`retrieve_knowledge`（/rag/search 用）的 QueryAnalyzer 维度过滤（doc_type/business_domain）在语料缺元数据时 0 命中且无放宽（ask 路径有）。

## C. Collection Resolver

kb_id 是逻辑知识库权威；实际 collection 由 `pgvector_store._collection_name_from_path` 单点推导（persist_directory basename），upload/search/chat 共用同一 pipeline 实例 → **单一权威成立，无需新增 resolver**（§12「不要另立第二套」）。修复落在主体判定与过滤放宽：

- `c4b865b`：subject_type 判定从「带部门」改为「已登录」——登录未声明部门 = employee+空部门（`authorized_kbs` 语义：只见 "all" 库 = policy_general）；guest/api-key 匿名通道保持 customer fail-safe（红线不动）。三处接线（tools/rag.py + routes/rag.py + routes/rag_search.py）同口径。
- `c4b865b`：retrieve_knowledge 向量腿 + BM25 腿在 QueryAnalyzer 维度 0 命中时按 kb 范围（kb_id/$or）放宽重试一次——**kb 隔离与权限过滤全程保留**，无跨库静默放宽。
- fail-fast：显式 kb 无数据 → EvidenceGate 明确拒答「知识库暂无相关资料」，绝不 silent fallback 到错误 collection（跨库兜底是 f17 设计行为且引用标明真实来源）。

## D. Upload E2E（全部 APISIX :9080 + 真实登录 JWT）

| 文件 | kb_id | department | status | chunks | metadata |
|---|---|---|---|---|---|
| p5-e2e-travel.md | policy_general | general | active/success | 2 | kb_id/department/permission_scope=general 完整 |
| p5-e2e-差旅报销.md | policy_general / policy_finance | general / finance | active/success ×2 | 1+1 | 完整 |
| p5-e2e-年假考勤.md | policy_hr | hr | active/success | 2 | 完整 |

app → Celery worker → rag-service → 每一跳 kb_id/department 无丢失（doc_registry 行核验）。

## E. Retrieval E2E（/rag/search）

`差旅报销上限 3,888 元` → top-1 = 上传文档 chunk（含「内部测试编号 KB-E2E-20260922」原文）；`差旅报销`/`员工差旅报销管理制度` 同样命中。

## F. Chat E2E（POST /api/chat/stream）

问题「根据公司制度规范，员工差旅报销的上限金额是多少？」→ 路由 RAG skill → kb 检索 → 回答：
`1.员工差旅报销的上限金额为3,888元[E1][E2]` + 参考文献 `p5-e2e-travel.md / p5-e2e-差旅报销.md（相关度 1.00）`，done 帧携带 sources（前端 SourceCard 数据源）。

## G. Isolation

- biz_order 显式查询：回答内容不含 policy_hr 的 7,777/HR-DOC-777 标记 ✓
- policy_hr 查询：返回年假内容，无 policy_general 差旅内容串入 ✓
- department/permission 红线未动：customer fail-safe（匿名）与 owner_depts 矩阵原样保留 ✓
- （数据事实：全量用户 dept 为空，员工带部门授权依赖管理端补录 dept → JWT → X-User-Dept 链路，代码已就绪）

## H. Long Input

6215 chars 长输入（背景+提问）→ 200 → Guard 通过 → Context Budget preflight（usage reported, 无溢出）→ RAG 命中 → 回答「3,888 元」+ 引用 ✓。
9415 chars → Guard token 档（8000）按新文案正确拦截 ✓（更新后的拒绝话术含 20000 字配置值与上传引导）。
25000 chars → HTTP 422 业务错误 CHAT_INPUT_TOO_LARGE ✓。

## I. Performance（无回退）

- 短聊天 Case1：200，正常完成。
- 长输入 Case8：6215 chars，preflight usage 0.75%，SSE meta/status/delta/done 全正常。
- RAG 检索：retrieve ~0.5s；ask 全链路（含 LLM 生成）10-30s 量级，与修复前一致。修复均为查询路径常数开销。

## J. Tests

- `PGPORT=5433 pytest context_budget/ memory/ config/ api/test_chat_input_limit.py tools/test_rag_subject_resolution.py security/ router_prefilter registry/layer/adr0001`：**366 passed, 0 failed**
- 新增：test_chat_input_limit.py 12 例 + chatInputLimit.test.ts 3 例 + test_rag_subject_resolution.py 7 例。
- 既有无关失败（与本任务无关、本任务未触碰文件）：test_pipeline_rebuild_pg 4 例 / test_processing_lineage_e2e 8 例 / test_embedding_singleton、test_indexer_trace、minimax passthrough 等（宿主 env/DB 状态依赖，HEAD 未改动即失败）。

## K. Git

| Commit | 内容 |
|---|---|
| f403be2 | fix(chat): harden long input validation（配置化 20000/64KB/8000 + 业务错误 + 前端对齐 + 测试） |
| c4b865b | fix(rag): 登录主体判定修正 + 向量/BM25 腿 0 命中放宽重试（并行会话按路径收录本会话工作区修复） |
| 37916b4 / 853979c | test(rag): 主体判定测试 |
| 73b4f8e | fix(guard): 拒绝文案从配置取值 |
| （本笔） | 本报告 |

剩余 dirty：`data/quality_records/*`、`frontend*/tsconfig.json`、`backend/tests/customer_service/test_order_service.py`、`frontend-admin/src/api/modelConfig.ts` 等均为**其他会话**文件，本任务全程路径限定未触碰（期间误 pop 的他人 stash 已完整恢复：47 个冲突文件逐一 checkout HEAD，stash 本体未 drop）。

## L. Final Verdict

```text
CHAT_RAG_PRODUCTION_READY=true
```

完成标准 14 项逐条对照：①长输入不再 422 ✓ ②仍有 20000/64KB/8000 硬限 ✓ ③超限业务错误 ✓ ④前后端一致 ✓ ⑤upload/search/chat 同一 pipeline 单点 collection 推导 ✓ ⑥kb_id 逻辑权威 ✓ ⑦无 silent doc_db fallback ✓ ⑧KB isolation ✓ ⑨department/permission 未绕过 ✓ ⑩上传→索引真实通过 ✓ ⑪Chat→RAG→Citation 真实通过 ✓ ⑫长输入→Context Budget→RAG 真实通过 ✓ ⑬APISIX 入口全量通过 ✓ ⑭Context Budget 守卫无回归 ✓

已知边界（后续域处理，非 blocker）：①长背景稀释查询时的 RAG 召回质量（§27 禁改算法域）；②前端聊天页 department 选择器在 IDENTITY_SOURCE=header 下对授权无效（body 字段被忽略，JWT dept 才权威）——建议引导用户在用户中心维护 dept。
