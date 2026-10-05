# 四份验收清单收官实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在同一工作会话内完成旅游、RAG 上传检索、客服、评测四份验收清单的剩余实证、缺陷修复、证据回写与终态判定。

**Architecture:** 先冻结四份权威清单的未完项盘点，再按“评测基础设施 → 客服业务与走查 → RAG 专项证据/可观测 → 旅游金标与体验”的依赖顺序逐项闭环。代码缺陷采用测试先行；现场验证使用现有 APISIX/容器/浏览器入口；每批代码、测试、报告和清单更新单独提交，避免把验收产物误纳入版本库。

**Tech Stack:** FastAPI、LangGraph、Celery、PostgreSQL/Redis、Docker Compose、Next.js、Vitest、Playwright、pytest。

**Spec:** `docs/reports/2026-10-05-双清单收官合并交接提示词.md`、`docs/reports/2026-10-05-客服验收终态交接提示词.md`、`docs/reports/2026-10-05-评测中心验收收尾交接提示词.md`、`docs/reports/2026-10-04-RAG上传检索验收清单.md`、`docs/reports/2026-10-04-旅游助手功能验收项清单.md`。

## Global Constraints

- 所有服务入口经 APISIX `:9080`，本机 PostgreSQL 使用 `5433`；局部 pytest 必须带 `--no-cov`。
- 不把测试上传、截图、临时 JSON 或数据库备份提交进仓库；已有用户未提交 `data/`、`gui-test-screenshots/`、`0` 保留不碰。
- Tool/Skill/Prompt/迁移等契约改变必须同步 lock、测试和报告；不批量把 `⚠️/◐` 改成 `✅`，每一项必须有可复核证据或明确终态豁免。
- 客服域 LLM 只能走 `backend.infra.llm.llm` 代理；旅游域不得把有状态流程注册成主图 Skill；RAG 不降低 E1 证据门阈值。
- 账号走查完成后，`uitest_user` 恢复为平台 `viewer`、客服档案 `agent`；所有验收夹具按 upload_id/doc_id/tenant 精确清理。

## Review Focus

- 生产门禁不能仅靠桌面清单文字变绿：验证 `*_PRODUCTION_READY` 与实际报告/脚本结果一致。
- 异步上传和重试不能静默丢任务：并发、同文件幂等、worker 失败、重启后 registry 必须可对账。
- 证据引用不能静默择一：冲突值要并列引用并明示，不得改成无证据作答。
- 客服的知识问答、办理分诊、停止/重试、订单数据源必须分别验证，不能用单一抽样替代。
- 旅游金标随产品演进更新时必须保留冻结版本、差异解释和全量回归结果，不能直接放宽断言掩盖回归。

## 当前未完项盘点

盘点基线：`git log -3` 为 `8ba346d/0e049bb/b3047c6`；代码与报告变更已提交，工作树另有历史验收产生的未跟踪 `data/*.json`、`gui-test-screenshots/`、`0`，不属于本计划。

1. **旅游**：四个金标用例与 core 文件锁定清单需按当前产品行为重判并完成全量 `tests/travel`；清单中第四批聊天框 LLM 化、多人偏好冲突 #68、若干“待复验”走查仍未形成最终证据。#15 天气 fault-injection 已完成，不能重复施工。
2. **RAG**：N1/N2/E6 已实证关闭，当前没有 `❌/⬜`；剩余是带证据的 `⚠️` 专项，包括完整解析/切分/结构/清洗扫描、embedding v2/失败重试/部分 batch、rerank 与 R3 人工复核、provider/poison/worker 故障、容量与背压、Q11 rewrite、A2/A4/A6/A7、O6-O11、T7/T8/T9、S4/S10 actor、D-6/E1 与 D-7 根因、API/N/S 观测以及数据/模型对账。目标是能实测的全部闭环；外部供应商或长窗口项目必须写成正式豁免而非伪造通过。
3. **客服**：A6 原始目标仍是唯一 Verdict false（当前冻结企业口径为假拒 10.1%/漏拒 16%，原 <10%/<5% 未达）；P2 订单查询仍存在直查 demo 库与 HTTP 网关混用；G2 gauge 触发断链；M8/M9、M10 30 组、多轮、N4 停止、N5 重生成、O3 停 rag-service、坐席端 J1/J8/N10 仍需点击级/全量证据；走查账号需恢复。
4. **评测**：桌面清单行已全 ✅，但尾部仍保留 `init_db` 迁移登记、enforce 灰度窗口、默认关闭守卫演练等历史 blocked_by 说明；需要验证 074/075 注册、发布 enforce 拒绝路径、守卫 off→on 演练、容器重建和评测回归，并把尾部状态改成与证据一致。

## 实施任务

### Task 1: 冻结基线、清单统计和证据目录

**Files:**
- Create: `docs/reports/2026-10-05-四清单收官盘点与实施计划.md`
- Create: `.superpowers/sdd/2026-10-05-four-acceptance-closure/progress.md`
- Inspect only: 四份权威清单、四份交接/施工报告、`AGENTS.md`

**Interfaces:**
- Produces: 四份清单逐项未完项表、证据路径约定、后续任务的基线 commit 与健康状态。

- [ ] 记录当前 `git status --short`、`git diff --check`、`docker compose ps`、四份清单状态计数；明确未提交数据夹具不纳入提交。
- [ ] 生成未完项盘点报告，逐项列出状态、证据缺口、施工动作、验证命令和最终判定规则。
- [ ] 提交盘点报告，不提交任何 `data/` 或截图夹具。

### Task 2: 评测中心终态复核与门禁演练

**Files:**
- Modify: `C:\Users\wh\Desktop\测评功能验收.md`
- Modify: `docs/reports/2026-10-04-评测中心验收收敛施工书.md`（仅补最终证据）
- Modify: `docs/reports/2026-10-04-评测功能企业级验收与优化报告.md`（仅补最终证据）
- Test: 评测相关 `backend/tests/` 门禁、迁移、生命周期、发布测试

**Interfaces:**
- Consumes: Task 1 的基线与当前 DB/容器状态。
- Produces: `EVALUATION_PRODUCTION_READY=true` 且 `blocked_by=[]` 仅在 074/075 登记、enforce 拒发、守卫默认 off/显式 on 三类证据全部存在时成立。

- [ ] 先写/补最小回归断言，再执行迁移登记检查、生命周期/发布/GATE/守卫测试并记录失败原因。
- [ ] 在隔离配置下做 regression gate、RAGAS gate、strict fields、cancel 和 tenant hook 的审计/拒绝演练；不伪造真实 Judge 分数。
- [ ] 重建 app/worker 后复跑评测核心门禁，核对容器内 commit/配置；通过后回写桌面清单尾部和报告。
- [ ] 提交评测代码/测试/报告路径，数据夹具不提交。

### Task 3: 客服业务缺陷、质量门和全量走查

**Files:**
- Modify: `backend/customer_service/**` 中订单查询、日报 gauge/告警、分诊/知识边界涉及文件
- Modify: `frontend-cs/**` 涉及停止、重生成、坐席端会话/双用户隔离的文件
- Test: `backend/tests/customer_service/**`、`D:/tmp/cs_*.py`、`D:/tmp/pwtest/*.mjs`
- Modify: `C:\Users\wh\Desktop\客服验收清单.md`
- Create/Modify: `docs/reports/2026-10-05-客服四清单收官报告.md`

**Interfaces:**
- Consumes: Task 1 基线；客服 API/前端现有测试账号。
- Produces: 订单查询统一 HTTP 数据源、可观测 gauge 可被 Prometheus 消费、M10 30 组证据、N4/N5/O3/J1/J8/N10 走查证据、A6 决策记录与 CS 终态。

- [ ] 先为 `_get_single_order` 的 HTTP 成功、网关无单、网关不可用写失败测试，再接通已有 HTTP 分支并回归。
- [ ] 先为 gauge 在 worker/beat 写入且 Prometheus 可读写失败测试，再修复 exporter/刷新器链路；验证告警规则触发与恢复。
- [ ] 执行 A6a 零成本混合检索并集重录；若仍不达原目标，按企业口径保留原目标不达并把 A6a 结果作为可审计决策，不能改阈值冒充达标。
- [ ] 回放 50 条知识问答、30 组 3~8 轮多轮集；完成停止副作用、重生成幂等、停 rag-service 降级和坐席双用户隔离走查。
- [ ] 恢复 `uitest_user` 角色，清理测试数据，刷新清单和 Verdict；只有知识质量和故障恢复均有证据才更新 `CS_PRODUCTION_READY`。

### Task 4: RAG 专项硬项、故障、容量和可观测收口

**Files:**
- Modify: `backend/rag/**`, `backend/tasks/**`, `backend/app/api/routes/rag_upload.py`（仅测试暴露的真实缺陷）
- Modify: `backend/tests/rag/**`, `backend/tests/` 对应测试
- Create: `backend/scripts/rag_acceptance_probe.py`（若现有脚本无法复用）
- Modify: `docs/reports/2026-10-04-RAG上传检索验收清单.md`
- Create/Modify: `docs/reports/2026-10-05-RAG四清单收官报告.md`

**Interfaces:**
- Consumes: Task 1 基线；现有 `D:/tmp/rag-acceptance/` 证据与 RAG reconcile 工具。
- Produces: 上传/检索/质量/可观测五门禁的逐项证据；代码可修复缺陷必须有 RED→GREEN 测试，外部不可控项形成编号、责任面、退出条件的正式豁免。

- [ ] 先对所有剩余 `⚠️` 建立机器可读矩阵，再分批执行结构扫描、embedding/批处理、rerank/重写、故障/容量、API/trace/faithfulness 探针。
- [ ] 对每个暴露的代码缺陷写失败测试并修复；保留 `rag_index_reconcile` 前后输出，重建 `rag-service` 与 `rag-index-worker` 后复验。
- [ ] 完成 actor/user_id、Q1/Q3 route 字段、O6-O9 trace 字段、T7 latency、S10 五动作审计核对；O11 若指标不存在则补埋点和测试，不把缺口改成通过。
- [ ] 清理验收夹具并复核 active/vector/chunk/BM25 一致；刷新 RAG 清单、缺陷卡和 `RAG_PRODUCTION_READY`。

### Task 5: 旅游金标、多人偏好和终态复核

**Files:**
- Modify: `backend/travel/**`、`backend/tests/travel/**`（按红测结果）
- Modify: `frontend/src/**` 旅游交互涉及文件（仅当点击/聊天验收暴露问题）
- Modify: `docs/reports/2026-10-04-旅游助手功能验收项清单.md`
- Create/Modify: `docs/reports/2026-10-05-旅游四清单收官报告.md`

**Interfaces:**
- Consumes: Task 1 基线；现有 `travel_v2` 金标与天气 fault-injection 证据。
- Produces: 全量旅游测试 0 failed、#68 多人偏好冲突有确定性规则/解释或 LLM 受控证据、所有“待复验”项有截图/脚本/报告、八个 PASS 与 READY 口径一致。

- [ ] 先单独运行当前失败的 golden/core 用例，确认是产品演进断言还是代码回归；若是演进，更新不可变 golden v2/v3 和差异报告，不能直接删除断言。
- [ ] 先写多人偏好冲突失败测试，再实现可解释的保留/折中/追问策略；运行 travel 全量测试和前端 TypeScript/Vitest。
- [ ] 完成 7-day layout、高并发、聊天/候选表等可执行走查；对实验性 LLM 化明确“本轮验收范围/排期”而不是把未施工项写成通过。
- [ ] 回写旅游清单统计、八 PASS、报告和最终 READY。

### Task 6: 四清单最终对账、审查与提交

**Files:**
- Modify: 四份权威清单及 `docs/reports/` 收官报告/索引
- Inspect: `git diff --check`、`git diff --stat`、完整测试日志、`docker compose ps`

**Interfaces:**
- Consumes: Tasks 2–5 的代码、证据、清单和报告。
- Produces: 一份四清单总对账报告；提交历史可追溯；最终状态只包含证据支持的 ✅ 或结构化豁免。

- [ ] 运行清单扫描：评测无 `❌/⚠️-primary`；RAG 无 `❌/⬜` 且每个 `⚠️` 有证据/责任/退出条件；客服/旅游没有未解释的 `⬜/🔜/进行中`。
- [ ] 运行四线最小回归、前端类型检查、核心服务健康检查；核对用户账号已恢复、临时数据已精确清理。
- [ ] 进行独立自审：逐条对照 Review Focus，修复重要问题后再跑相关测试；保留未能闭环的真实外部阻塞，不用文案消除。
- [ ] 提交最终对账报告和代码文档；输出 commits、测试命令、证据目录、四项终态及剩余正式豁免。
