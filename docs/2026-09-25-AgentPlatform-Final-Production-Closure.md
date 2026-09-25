# Agent Platform — Final Production Closure 报告

> 日期：2026-09-25 ｜ 执行：Final Production Closure Mode（F0→F8）
> 区间：起始 commit `addaa32`（工作区含 7 散件）→ 终止 commit `8717805`＋本文档提交
> 原则：少改代码、缺口关闭、实机验证、证据落库；**绝不把没有完成的 Gate 写成 PASS**

---

## 1. Verdict

```text
PROJECT_INTERNAL_CLOSURE_PASS=false
PRODUCTION_RELEASE_GATE_PASS=false
PROJECT_CORE_FROZEN=true
EXTERNAL_BLOCKERS_PRESENT=true
FULL_ROADMAP_COMPLETE=false
GLOBAL_REGRESSION_PASS=false          ← INTERNAL 的唯一内部阻塞项（存量测试债，见 §5.3）
HISTORICAL_13_FAILURES_RECHECK_PASS=true
NEW_REGRESSION_COUNT=0
```

**为什么 INTERNAL=false 却 CORE_FROZEN=true**：判定规则（计划书 §二十）要求 INTERNAL 至少含 Global regression PASS。全量回归 105 failed 中 13 例历史 WIP 已复绿、2 例 flaky 复跑即绿、其余 ~104 例为**基线内既有**的陈旧 mock/环境依赖测试债（本轮逐组定性，非产品回归，本轮零新增）——但按字面口径 GLOBAL_REGRESSION_PASS=false，故 INTERNAL 如实=false，并把「存量测试债清偿」登记为 INTERNAL 转真的唯一内部路径。所有其他遗留项均已归类（DEFERRED/EXTERNAL/BLOCKED/ACCEPTED_RISK），核心可冻结。

## 2. Frozen Baseline

| 项 | 值 |
|---|---|
| 起始锚点 | addaa32（Phase3 STOP D）+ 2a9b018（STOP L）+ c322ac3 —— 均验证为 HEAD 祖先 |
| 本轮新增提交 | 62c2b39(F0 六组散件) → 62e35fe(F1 STOP E) → 9fead2d(F2 SSE) → 5007b43(F3 演练+P1-7 修复) → 8717805(F4/F5 收口) → 本文档 |
| 迁移 | 000→052 不变（**本轮零迁移**：STOP E 收敛为纯代码闭环） |
| 容器 | app/worker 镜像含本轮热部署补丁（docker cp + restart，与 HEAD 代码对齐）；**下次构建镜像需纳入本轮 9 个代码文件**（镜像漂移债 X-6 同类收窄） |

## 3. Closure Matrix（遗留项 → 终态）

| 原 ID | 项 | 终态 | commit/证据 |
|---|---|---|---|
| F0 | 工作区 7 散件（超盘点 5 件） | **RESOLVED** | 6 组 pathspec 提交 62c2b39；旅游域 581 绿归因实测 |
| P-1 | Phase3 STOP E（verifying/IN_DOUBT 人工 reconcile） | **PASS+FROZEN** | 62e35fe；缺口 G-E0~E4 关闭（主缺口=既有裁决通道对 failed+UNCERTAIN 主形态 no-op）；18 测双环境+权威库 CLI 闭环实证 |
| C-1 | 客服 SSE 恢复协议 | **PASS+FROZEN** | 9fead2d；seq 帧化+注册表+resume 端点+前端双端续流；**APISIX 真链路 100 次断线验收 PASS**（五零+403/404 探针+13 次重复交付去重吸收） |
| C-2 | 真实 PG 停止/恢复演练 + Worker 强杀 ×20 | **PASS（含 P1-7 实锤修复）** | 5007b43；P1/P2/P3/P4/P6 PASS，P5 BLOCKED_IN_THIS_TOPOLOGY 如实；强杀 20 轮：11 在飞全恢复/9 完成后无害/零丢失零双终态 |
| T-3 | 历史 13 例复绿复核 | **PASS** | 118→105 差集取证：confirmation_store×7+defect6×6 全部复绿（addaa32 消化确认） |
| R-3/R-4 | 审查 P1/P2 对账台账+待验证闭环 | **PASS** | `docs/2026-09-25-代码审查最终对账与关闭报告.md`：P0 5/5、P1 16/16、P2 34/34、待验证 10/10 全七态归类，零未分类 |
| F4 回归 | 全量回归 | **GLOBAL=false（如实）** | 105 failed＝104 persistent（存量 mock/环境债，逐组定性）+2 flaky（复跑绿）；NEW_REGRESSION_COUNT=0 |
| C-5/F6 | 灰度四阶段 | **INFRA_READY=true / PASS=false** | `docs/2026-09-25-F6-灰度发布机制与Gate定义.md`；24h×4 观察窗未启动，BLOCKED_BY_OBSERVATION_WINDOW |
| M-1 | quarantine 169 条 | **RUNTIME_RISK_CLOSED / DATA_DISPOSITION_REQUIRED** | 实测 169 行 tenant_id='quarantine'（迁移保留哨兵）；运行时永不产出/永不可达；处置（DELETE/ARCHIVE/KEEP）待产品决定，不阻塞发布 |
| X-2 | llm_models.context_length 全 NULL | **登记（放大窗口前必须补填）** | 原样（上下文线 FROZEN 边界） |
| T-2 | STOP M（支付/取消/退款） | **STOP_M_DEFERRED=true** | 产品能力扩展，非生产核心缺口；Booking 交易核心已独立冻结 |
| C-6 | P6 多模态 | **CS_MULTIMODAL_DEFERRED=true** | 前置条件（文本链路 100% 稳定 7 天）未满足 |
| C-3/C-4/C-7 | 客服 P2 降本/P4 交付校验/缺陷收口尾巴 | **DEFERRED** | 成本/质量优化项，不阻塞发布安全 |
| R-2 | API Key 服务端轮换 | **DONE（2026-09-25 补做，API_KEY_ROTATION_PASS=true）** | 新 key 生成替换 4 处配置 → app/mcp-service 容器重建 → 三 BFF 重启；旧 key 全路径 401、新 key 全链 200 |
| T-1 | 真实 Booking 供应商 | **BLOCKED_BY_EXTERNAL_BOOKING_PROVIDER**（维持） | 契约/registry/评测驱动就绪，签约即接 |

## 4. Changed Files（按业务线）

- **幂等/STOP E**：`backend/shared/idempotency.py`（双形态裁决族）、`backend/customer_service/reconciliation.py`（新，确认行收敛）、`backend/customer_service/repository/confirmation_repo.py`（未动——收敛走独立模块）、`backend/travel/booking/reconciliation.py`（Model C 账本真实收敛）、`backend/scripts/idempotency_ops.py`（verifying/inspect/resolve-confirmation 三子命令）
- **SSE Resume**：`backend/app/api/routes/chat.py`、`backend/app/api/stream_resume.py`（新）、`backend/config/settings.py`、`backend/observability/metrics.py`、`frontend/src/api/chat.ts`、`frontend-cs/src/api/chat.ts`
- **任务运行时**：`backend/orchestration/checkpoint/task_executor.py`（P1-7 连接池）
- **测试/驱动**：`tests/test_stop_e_verifying_reconciliation.py`（新）、`tests/travel/booking/test_stop_e_manual_resolve_ledger.py`（新）、`tests/api/test_sse_resume.py`（新）、`scripts/e2e_sse_resume.py`（新）、`scripts/e2e_final_drills.py`（新）
- **文档/口径**：AGENTS.md、项目架构总览、platform-runbook、四层设计规范（E10 例外）、.env.example、F2 设计/F6 灰度/STOP E/对账报告/本文档

## 5. Real Runtime Acceptance（全部实机）

### 5.1 SSE ×100（APISIX :9080 + JWT + 真实 LLM，506s）
missing_event=0 / duplicate_terminal=0 / terminal_mismatch=0 / cross_request_replay=0 / cross_user_replay=0；13 次 at-least-once 重复交付全部被 seq 去重吸收；未知流 404、他账号游标 403 实证。逐轮记录 `C:\Users\wh\AppData\Local\Temp\sse_resume_100.json`。

### 5.2 Worker SIGKILL ×20（kill -9 PID1 崩溃语义）
11 轮命中在飞（RUNNING 相位确定性打击）→ 全部自动重启 + lease 接管 + sweep 重投 → SUCCESS（execution_id 换发、recovery=1、terminal 行恰 1）；9 轮击杀落在自然完成后 → 无害；lost_task=0、stuck=0、double_terminal=0。

### 5.3 PG 停止/恢复矩阵（docker stop/start 真实拓扑）
P1 请求前 down（有界响应+恢复自愈）✓｜P2 执行中 down（不崩进程、error 如实）✓｜P3 任务终态前 down（**修复后**：恢复后新任务 SUCCESS）✓｜P4 裁决遇 down（原子干净失败，恢复后同一裁决恰好成功）✓｜P5 booking CAS down＝BLOCKED_IN_THIS_TOPOLOGY（环境未注入 booking 开关，离线探针 B 系列钉死同类语义）｜P6 收敛（health/worker/migrations/stuck=0）✓。

### 5.4 全量回归（宿主机默认环境，25m34s，7030 passed / 105 failed / 3 errors）
- 历史 13 例（confirmation_store×7 + defect6×6）：**全部复绿**（与 STOP L 基线差集取证）；
- 2 例（test_state_transition 1 + test_metadata_shadow 1）：复跑 9/9 绿＝flaky；
- 其余 ~104：基线既有 persistent（陈旧 mock 契约/环境依赖，如 sql_tool 断言旧契约、rag_upload 401），**本轮零新增**；
- STOP E/SSE/幂等/booking/契约/registry 各线套件全绿（含容器内权威库复跑）。

## 6. External Blockers（不混入代码缺陷）

| 阻塞 | 判定 | 解除路径 |
|---|---|---|
| 真实 Booking 供应商签约 | BLOCKED_BY_EXTERNAL_BOOKING_PROVIDER=true | registry 登记+新适配器+G45/G46 实机 E2E |
| API Key 服务端轮换 | **已完成（2026-09-25 补做，API_KEY_ROTATION_PASS=true）** | 新 key 生成并替换 4 处配置（.env + 三前端 .env.local）→ app/mcp-service 容器重建 → 三前端 BFF 重启；**旧 key 全路径 401（作废实证）+ 新 key 全链 200（网关/直连/三 BFF 代理）**；git 历史仍含旧值但已死（本仓库本地私有，风险接受）。剩余外部阻塞：灰度观察窗、Booking 供应商 |
| 灰度观察窗 24h×4 | BLOCKED_BY_OBSERVATION_WINDOW=true | 按阶段定义执行并逐小时采样 |
| （登记）Grafana 口令/RBAC 收口 | 生产部署前置 | GRAFANA_PASSWORD 注入；业务写端点角色闸 |

## 7. Deferred Roadmap

STOP M（支付/取消/退款）、P6 多模态、CS P2 路由降本、CS P4 交付校验、客服缺陷收口 Step7/8、上下文线 X-1/X-2/X-5/X-6、审计测试债（本轮归类的 27 项 P2 OPEN+P1 #3/#4）。

## 8. Known Accepted Risks（摘要，全量见对账报告）

| 风险 | 缓解/触发条件 |
|---|---|
| resume_decision 死通道（P1-3） | TRAVEL_USER_DECISION_INTERRUPT=false 默认关；启用前必须补写入侧 |
| 主图/CS/旅游 checkpointer 裸连接（P1-7 残余面） | 开关门控默认关（MemorySaver 降级）；启用持久化前必须换 psycopg_pool |
| 财务 SQL 旁路坏 SQL | FINANCIAL_SQL_BYPASS_ENABLED=false |
| 断连后流跑完 token（F2 行为变更） | 可恢复的必要代价；背压/abort 语义不变 |
| 宿主机→docker PG 连接 ~5s/个 | 测试走容器第二路径；生产无此问题 |

## 9. Production Runbook（增量）

见 `docs/platform-runbook.md`（本轮修正 PG-down 行为描述）+ `docs/2026-09-24-Side-Effect-Idempotency-Production-Runbook.md` §10（STOP E 裁决全流程）+ F6 文档（灰度回滚程序与发布检查单）。要点：发布仍走 `scripts/release.sh`（先迁移后应用，12 Gate）；新增 resume 端点与裁决 CLI 的运维入口已在 runbook 内。

## 10. Final Git State

```
$ git status --short
（clean —— 本报告提交后为零未提交文件）
```

本轮全部工作按业务线 pathspec 提交，无跨线收编；工作区在 F0 后保持 clean，F1-F5 每阶段完成即落库。

## 11. Reopen 条件（INTERNAL=true 的唯一内部路径）

清偿 ~104 例存量测试债（陈旧 mock 契约为主，建议按目录分批：sql_tool/rag_upload/email 族），或经产品确认以「owned-scope 回归」口径重定义度量——届时 GLOBAL_REGRESSION_PASS 可转 true，INTERNAL 随即转 true（其余九项门均已 PASS）。
