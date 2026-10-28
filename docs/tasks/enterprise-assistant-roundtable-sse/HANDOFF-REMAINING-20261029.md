# 剩余专项交接：发货/发票措辞 + 供应商冷启动韧性（2026-10-29）

> 本文是给新会话的两项专项任务书。背景：发布门禁已 100%（24/24），rag.qa v17 与 self_correction v2 已发布（六进程快照核验），P95 压测与 browser-use 验收已通过，全部成果已合并本地 main 并推送远端（origin/main=b3d8f1d）。支付问是全链路健康的对照组。完整历史见同目录 PROGRESS.md 与仓库根 ZCODE_HANDOFF.md。

## 〇、环境速查（两项通用）

```text
工作树：C:\Users\wh\.codex\worktrees\assistant-roundtable-sse\agent
分支：codex/enterprise-assistant-reliability-fixes（全部已合并 main=origin/main=b3d8f1d）
运行栈：docker compose 项目 agent（agent-app-1 :8000、rag-service :8090、apisix :9080、pg :5433）

# 代码生效唯一路径（cmd.exe/devctl.bat 在本机失效，用 compose 直调）：
cd worktree 后执行：
COMPOSE_FILE='docker-compose.yml;部署/prod-4c8g/docker-compose.local.yml' \
COMPOSE_PATH_SEPARATOR=';' docker compose up -d --build app business-mock apisix rag-service \
  mcp-service agent-worker rag-index-worker maintenance-worker report-worker beat cs-dispatcher
# rebuild 后必核验：curl 127.0.0.1:8000/health、容器内 DATA-01 链（manifest/suite/cases hash 一致）

# 认证：
#   后端直连 API（8000）→ 请求头 X-API-Key（worktree .env 的 API_KEY 行）
#   BFF/网关链路（:3200/api，含 trace 查询）→ 登录换 JWT：
#     凭据文件 C:/Users/wh/Desktop/local_super_admin-temp-password.txt（账号 local_super_admin，只在内存用）
#   页面测试账号（已注册）：13800002029 / Aa12345678（3100 主前端）
# trace 查询：GET http://127.0.0.1:3200/api/observability/traces/{trace_id}（带 JWT）
# 限流注意：chat 路由每用户并发 3 + 30 次/分钟，压测脚本别超

# prompt 治理链（rag.qa 当前已发布 v17、self_correction v2，改动必须走链）：
# POST /prompts/{key}/drafts → POST /prompts/{key}/versions/{n}/release
#   {"suite":"desktop_upload_full","evaluation_mode":"semantic","executor":"local","target_env":"production"}
# → 等 release status=passed（约 6-8 分钟）→ approve → publish
# 发布评测已修复模板注入：草稿版本会被真实评测（此前「评旧版」缺陷已根治，见 306d1b1）
```

---

## 专项一：发货/发票措辞（知识库问答对日常措辞失败）

### 症状与复现

向企业助手（页面 :3100 或 SSE POST :3200/api/chat/agent/stream，`{"question":..., "session_id":"...", "request_id":"..."}`）发送：

| 措辞 | 结果 | trace |
|---|---|---|
| 请检索企业知识库：企业支持哪些支付方式？请给出处。 | **PASS（对照组）**：4.6s 回答四类支付方式+FAQ 出处 0.92 | 8518f4310193 |
| 请检索企业知识库：企业订单的发货时效是怎么规定的？请给出处。 | FAIL：进 rag.search 但拒答「知识库中未找到…（已尝试改写提问重新检索）」 | 33ea3bdceec0（父）/ d904053b1459（rag 子） |
| 客户下单后多久发货？请给出处。 | FAIL：**没进知识库**，直通 general_chat 答通用废话 | b15c7a898351 |
| 企业申请发票需要税号和开户银行吗？请给出处。 | FAIL：拒答（同措辞 10-27 曾成功，trace 07c85a58dc1d） | 9ce5e7eb33a0 |

数据在库且正确：`chunk_store`（pg :5433/agent_memory）policy_general/e07b25dc7b#0 =「Q：客户下单后多久发货? A：现货商品 24 小时内发出，预售商品以商品详情页标注的发货时间为准。」发票 FAQ 同文档。

### 时间线定性（重要：既有缺陷，非 v17 回归）

- 扫描近 3000 条 trace：同措辞失败自 **2026-10-27 19:17** 起持续（44 次命中全部失败）。
- 10-28 晨基线 D:/tmp/agent-baseline-20261028.json 的 shipping/invoice 即已失败——早于本轮全部改动。

### 根因（两个独立缺陷，都在 rag.qa 上游）

1. **领域判断误分流**（b15c7a898351）：「客户下单后多久发货？」被 embedding 领域判定为 `domain=general, source=product_scope`（仅 65ms），路由 `policy_skip` 直通 general_chat。trace 里无任何检索 span。10-27 曾修过支付问同类 prefilter FAIL，发货/发票的 FAQ 词（下单/发货/多久）仍漏。定位：路由的领域判断/产品域分流（trace span「领域判断」「路由决策」，代码 grep `product_scope`）。
2. **rerank 措辞泛化不足 + 自纠错二检无效**（d904053b1459 完整链路）：进 rag.search 后，首检候选仅 5 块（Dashscope in=5），重排 output=1（top1=0.4076，是政策退款条款不是发货 FAQ；阈值 0.3，`backend/rag/reranker.py` RERANK_SCORE_THRESHOLD）——FAQ 块要么没进重排池要么分数低于阈值被截；生成端 can_answer=false 后 self_correction 改写为「企业订单的发货时效规定」重检，**二检 0 文档**，最终拒答。要点：问法「发货时效是怎么规定的」与 FAQ 原文「多久发货」同义不同词，单查询检索命不中；生产链虽有 MultiQuery 层（rag/chain.py，位于 Rerank 内层）但对该查询没起作用，需查实际是否启用/改写质量。注意评测链（get_full_retriever）默认 multiquery=False，评测 24/24 不含此场景——修后不要指望门禁套件覆盖它。

### 修复方向（供参考，按马斯克五步先删后简）

- 缺陷 1：产品域分流的词面/embedding 判定把「下单、发货、发票、退款」类电商 FAQ 词兜进 knowledge 域；对照支付问为何通过，找差异点后最小修改。不要放宽成全直通 knowledge（会破坏 general_chat 边界）。
- 缺陷 2 三选一或组合：①查生产 MultiQuery 为何无效（开关？改写质量？）；②self_correction 二检零文档时降 rerank 阈值或改用向量纯检兜底一次（现有失败与降级规范 docs/architecture/failure-degradation.md 的精神：宁可给出带出处的低置信答案或明确「未找到原因」，不要连环无效重试）；③rerank 阈值 0.3 对 FAQ 短文本的打分偏低的校准（动全局阈值风险大，优先 ①②）。
- 提示词侧不要动 rag.qa v17——它在本场景的行为是正确的（证据不匹配时 can_answer=false 是对拒答边界的忠实执行）。若确需动，建 v18 走完整治理链。

### 验收标准

两种失败措辞（表格第 2、3 行）+ 发票措辞各发一次：发货答案含「24 小时」并带 e07b25dc7b FAQ 出处；发票答案含税号/开户银行带出处；支付问回归仍 PASS；general_chat 的无关问题（如「用一句话解释幂等」）仍走 general_chat 不被误吸入知识库。保留 trace id 与 D:/tmp 证据。

### 排障提示

- 生产链路无 FusionDebug/RerankDebug（那是 EVAL_TRIGGER=prompt_publish 门控的评测期诊断）。要观测生产检索细节：拉 rag 子 trace（父 trace 的 rag:rag.search span 里 child_trace_ids），看「增强混合检索」「Dashscope」「Evidence Gate - Rerank」「Self-Correction Rewrite」各 span 指标。
- 裸 `docker exec python` 起检索探针会因 embedding 供应商解析失败起不来（需 app 进程上下文）——别走这条路，用真实 SSE 请求+trace 观测。
- 可临时在容器内打点或用管理端 playground（:3200）做单环节验证，改完必须 rebuild+健康核验。

---

## 专项二：供应商冷启动韧性（首问延迟尖峰 122s）

### 症状与证据

- P95 压测（D:/tmp/agent-p95-battery-20261029.json）**首轮** explain 题（general_chat，doubao-seed-2.0-mini）总耗时 122.5s，答案本身正确。trace fa64e192291a：duration_ms=122946，耗时几乎全部落在 general_chat 单 span（elapsed=122699ms），即云供应商 LLM 调用内部；无路由/检索异常。
- 特征：像 SDK 层超时叠加重试（PROGRESS 10-27 有先例记录：「SDK 自动重试会叠加客户端已超时而供应商仍计算的请求」）。**全天 60 串行+15 并发仅此一次**，压测前栈已重建约 40 分钟且有间隔流量，非容器冷启动，是供应商侧/调用链的首呼异常。
- 影响口径：违反「反馈≤1s、普通解释 2-5s」目标的最坏情形；用户视角就是首问卡两分钟。

### 已知机制与定位入口

- 模型：doubao-seed-2.0-mini（云，DB 绑定）。LLM 客户端/超时/重试配置在 backend/infra/llm/ 与 backend/config；general_chat 生成在 orchestration graph 直执行链。
- 已知同类事实：评测生成侧曾把 SDK max_retries=0、HTTP 批大小 1 解决过叠加超时（10-27 记录，评测客户端）；在线客户端默认超时/重试未动过。
- 已有的正确做法参照：发布评测的 _warm_external_models（embedding/rerank 预热）——LLM 生成侧没有对应预热或快速失败机制。

### 修复方向

1. 先定位后动手：给 general_chat 的供应商调用补齐可观测（provider_call_ms/retries/attempt 序号是否已在 trace？不齐则先补埋点），复现一次抓实参。
2. 复现手法：重建栈后立即发首问；或让栈闲置 30+ 分钟后发首问；多次采样。
3. 候选修法（按风险从小到大）：收紧单次调用超时让失败快速暴露 → 显式控制重试次数/退避（对齐失败与降级规范，用户侧先给反馈话术）→ 首呼预热（进程起来后异步打一次最小 LLM 请求，类似 _warm_external_models）。禁止：把超时调大到「等它算完」、吞异常装成功。
4. 顺带确认：这 122s 内用户账本是否被重复计费（重试叠加时 llm_usage 可能多行）——llm_usage 表按 trace_id 查。

### 验收标准

冷栈/闲置后首问 P95 显著改善（目标：首反馈≤1s 不变、总耗时进入普通解释档 2-5s 或至少 ≤10s 且有过程反馈）；trace 能回答每次尝试的耗时与次数；无重复计费。跑一轮 mini 采样（如冷启动 3 次+热态 5 次）留数据。

---

## 纪律（沿用仓库规则）

- 提交 `git add -A -- <paths>` + `git commit -m "..." -- <paths>` 双重路径限定；完成即合并 main；推送需走代理 `git -c http.proxy=http://127.0.0.1:7897 push origin main`（直连 443 超时）。
- 评测数据改动走 DATA-01 三处同步；prompt 改动走 drafts→release（真实评测）→approve→publish；不跑默认全量回归。
- 不掩盖失败：修不动的定性如实记录，不伪造 PASS。
