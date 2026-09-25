# Final RC — Test Debt Closure 阶段状态报告（STOP A 进行中）

> 日期：2026-09-25 ｜ 会话：Final RC 执行会话 ｜ HEAD：ef85ba8（Final Production Closure 提交链 0afcd4c 为其祖先，基线锚点正确）
> 性质：**如实状态报告**。本轮目标 = 清偿 ~104 例 persistent 测试债 → GLOBAL_REGRESSION_PASS=true → PROJECT_INTERNAL_CLOSURE_PASS=true。
> **当前状态：STOP A（基线重建）未完成，正在以分块策略推进；STOP B-F 未开始。**

---

> **继续执行增量（2026-09-25，后续会话）**：本报告以下正文保留当时的基线快照；当前 HEAD 已推进至 `52a9b69`，并补充了经单测证实的连接超时、旅游 run-id、LLM 契约及 MiniMax 默认端点修复。STOP A 仍未完成，新增证据与当前判定见文末 §8。

## 1. Verdict（截至本报告时刻）

```text
FINAL_RC_STOP_A_PASS=false        ← 基线未跑完（travel 块确定性挂死未普查、5 个块待跑）
GLOBAL_REGRESSION_PASS=false
PROJECT_INTERNAL_CLOSURE_PASS=false
NEW_REGRESSION_COUNT=未定义        ← 待 baseline 完成后对比
TEST_DEBT_UNCLASSIFIED=未开始      ← STOP B 才做
PRODUCTION_CODE_CHANGE_COUNT=0    ← 本轮至今零生产代码改动（符合红线）
```

---

## 2. 已完成的工作

### 2.1 环境与口径确认（STOP A 前置，全部实证）

| 项 | 结论 | 证据 |
|---|---|---|
| git 状态 | 工作区 clean，HEAD=ef85ba8，Final Closure 提交链在祖先 | `git status`/`git log` |
| 并发会话检查 | 宿主机无其他 python/pytest 在跑 | tasklist |
| 容器栈 | 全部 healthy（app/worker×6/beat/CS dispatcher×2/PG/Redis/APISIX 等） | `docker ps` |
| PG 真实拓扑 | docker `agent-postgres-1` 权威库映射 **127.0.0.1:5433**；`.env` 的 PGPORT=5432 是容器视角 | `docker port` |
| 上轮口径还原 | 上轮 7030/105/3（25m34s）须在 **`PYTHONPATH=backend PGPORT=5433` + `-n 4`** 下取得 | 见 §3.1-3.3 |

### 2.2 全量回归启动尝试（4 次整跑 + 分块策略，详见 §3）

产物目录 `artifacts/final_rc/`：

```text
BASELINE_RUNBOOK.md               ← 锁定口径与依据（后续 STOP E 复跑必须同口径）
baseline-collect-broken-run1.log  ← 无 PYTHONPATH：13 collection error 中断（口径反证）
baseline-singleproc-slow-run2.log ← 单进程慢跑残段（9min/5%，证上轮必为 -n 4）
baseline-hang-run3-partial.log    ← -n 4 整跑残段（73% 挂死 + py-spy 取证前）
chunks/                           ← 分块驱动 v2 + 各块日志/summary
```

### 2.3 分块回归有效结果（已完成 7 块）

| 块 | 内容 | passed | failed | error | skipped | 用时 | 备注 |
|---|---|---:|---:|---:|---:|---:|---|
| cs | customer_service（1012） | 1004 | 6 | 1 | 2 | 482s | 至少 1 条失败=PG 连接被打满（环境污染，待甄别） |
| rag | backend/tests/rag（783） | 767 | 14 | 0 | 2 | 256s | |
| orch | orchestration（669） | 668 | 1 | 0 | 0 | 471s | |
| api | backend/tests/api（445） | 434 | 11 | 0 | 0 | 1233s | attempt1 于 97% 停滞被杀，attempt2 完整跑完 |
| eval | evaluation+eval+prompts（413） | 298 | 2 | 1 | 112 | 45s | |
| sql | backend/tests/sql + user_context（297） | 296 | 1 | 0 | 0 | 67s | |
| infra | infra+config+observability+audit+skills（473） | 463 | 9 | 0 | 1 | 72s | |
| tools | tools/tool_runtime/services/workers 等（444） | 414 | 29 | 1 | 0 | 103s | v1 时代产物，尾部 summary 行完整，判有效 |
| **小计** | | **5344** | **73** | **3** | **117** | | |

### 2.4 基础设施产物

- `artifacts/final_rc/run_chunked.sh` v2：分块驱动（停滞检测 8min + 硬上限 25min + PowerShell 按 cmdline 杀树 + 自动重试一次 + HANG 标记跳过）。
- `docs/2026-09-25-AgentPlatform-Final-Production-Closure.md` §11 的 Reopen 路径即本轮：sql_tool/rag_upload/email 族的目录分批建议与本分块结构一致。

---

## 3. 发现的问题（按根因层排列，全部实测）

### 3.1 口径层：双 `tests/` 目录的 namespace package 抢占（最重要的口径发现）

仓库同时存在：
- 根 `tests/`（历史遗留，**无 `__init__.py`**，namespace package，内含空 `tests/sql/`）
- `backend/tests/`（regular package，有 `__init__.py`）

**不设 `PYTHONPATH=backend` 时**，13 个测试模块
（`backend/tests/sql/test_sql_scope_*.py` 等的 `from tests.sql.conftest import ...`、
`backend/tests/context_budget/test_stop_*.py` 的 `from tests.context_budget...`）
在收集期 `ModuleNotFoundError: No module named 'tests.sql.conftest'` → **`Interrupted: 13 errors during collection`，全量根本无法启动**（实测 7205 collected + 13 errors）。

设 `PYTHONPATH=backend` 后：**7395 collected, 0 errors**。
数字对账：7395 − 67（13 个 error 模块内用例）= 7128 ≈ 上轮 7030+105+3=7138（差 10 属 skip/参数化统计口径），证明上轮即此口径。

### 3.2 环境层：宿主机 → docker PG（vpnkit 转发）链路病态，DB 密集段确定性挂死

- 实测三整跑（run3/4 + 分块 travel×3）全部挂死于 **`psycopg connect` 的 `wait_conn select`**（py-spy dump 实锤 4 worker 同栈：`backend/tests/test_stop_e_verifying_reconciliation.py:110`、`test_email_durable_idempotency.py:64`、`test_side_effect_idempotency.py:86`、`services/task_service.py:37`）。
- TCP 层 `connect()` 瞬时成功（0.00s），PG 健康时真实连接 0.09s OK——挂死发生在 vpnkit 转发层（半开 socket 无限等待，psycopg 无 connect_timeout 默认无限等）。
- PG `max_connections=100`，业务容器常驻占用 ~52（空名 idle 30 + agent-app 17 + 认证中 5）；测试 4 worker 的 psycopg2 直连**无池上限**，DB 密集块瞬时打满 100 → `FATAL: sorry, too many clients already`（cs 块已实拍）→ vpnkit backlog 满后升级为挂死。

### 3.3 确定性挂死点（分块跑暴露的新事实）

| 块 | 挂死位置 | 特征 |
|---|---|---|
| travel（629 条） | **80%，进度点数三次完全一致**（51 点） | 确定性 hang，非随机抖动；块内某测试必然无限等 PG |
| api（445 条） | **97%**（最后一两个测试） | 同上；attempt2 却完整跑完 → 与同块内其他测试的执行顺序/资源残留相关 |

这两处是**待普查的 census 缺口**：travel 块目前无任何有效失败清单，api attempt1 的 97% 停滞影响用例未单独取证。需在 STOP A 收尾用二分法（按文件/按用例）定位并单独补测。

### 3.4 工程层：Windows 驱动脚本的三个坑（已修复/已知）

1. **GNU timeout（Git Bash）`--signal=KILL` 在 Windows 不生效**：到点后无法终止 pytest 进程树，主进程与 worker 全部存活（进程表实锤）。v2 已弃用 timeout，改「后台 + log 停滞检测 + PowerShell `Stop-Process` 按 cmdline 过滤杀树（模式 `pytest|exec.eval`，后者覆盖 xdist worker 的 `-u -c "exec(eval(...))"` 特征）」。
2. **驱动脚本主循环杀不死的假象**：杀掉 pytest 后 bash 循环自动重试拉起新 pytest，表现为「python 杀不完」。必须先杀驱动 bash（按 cmdline match `run_chunked`）再清 python。
3. **清理时误伤自身 shell 链**：PowerShell 按 cmdline match 杀 bash 时把 ZCode 工具调用的 bash 链（`snapshot-bash...` 特征）一并终止，导致驱动后台任务报 failed（exit 4294967295）——无害但需知情；本次连带效应 = v1 死前产生 4 个 0B 残块（api/eval/sql/infra 的 attempt1）+ travel 假 `RC=0`。**v2 已重跑覆盖全部残块**（api/eval/sql 已有有效 summary，infra 跑至一半时本报告落笔）。

### 3.5 失败清单中的环境污染必须甄别（STOP B 前置约束）

cs 块 6F+1E 中已实锤至少 1 条（`test_business_guard.py::test_d3_recovery_execution_id_keeps_identity`）traceback 为 `Connection refused` + `too many clients already`——**环境故障，不是测试债**。
已立甄别规则：凡 traceback 含 `too many clients / Connection refused / connection to server` 的条目，在块跑完后用 `-n 1` 小批量单独复跑；复跑绿 → 剔除出测试债清单并记录为环境污染（不美化、不隐藏）。

---

## 4. 当前基线数字（PARTIAL，仅已完成 7 块，不可用于判定）

```text
PASSED=5344（8 块小计）
FAILED=73
ERRORS=3
SKIPPED=117
待跑：tools（重跑中）、platform、root-a/b/c/d（含根 tests/ 55 条）
待补：travel 块二分普查（确定性挂死定位后补完整清单）
对账目标：上轮 7030 passed / 105 failed / 3 errors（7395 collected 口径）
```

**注意**：已完成块的 failed=64 已超上轮 105 的六成，分布（tools 29F、rag 14F、api 11F）与上轮「sql_tool/rag_upload/email 族」提示的目录分布大体相容但更分散，最终对账以全部块完成后的并集为准。

---

## 5. 未完成的计划

### STOP A 收尾（当前）

- [ ] infra/tools/platform/root-a/b/c/d 六块跑完（驱动 v2 自动进行中，tools 平台位重跑）
- [ ] travel 块二分定位挂死测试（按文件逐个 -n 1，定位后单独取失败清单）
- [ ] api 块 97% 停滞用例单独补取证
- [ ] 全部 FAILED/ERROR 按 §3.5 规则甄别环境污染，小批量复跑剔除
- [ ] 汇总 baseline：TOTAL_PASSED/FAILED/ERRORS/SKIPPED + `baseline-failures.txt`/`baseline-errors.txt`
- [ ] 与上轮 105F/3E 对账（差集=本轮新增回归初步信号，NEW_REGRESSION_COUNT 初判）
- [ ] 建档 `docs/2026-09-25-FinalRC-TestDebt-Baseline.md`（本文件转正为基线文档）
- [ ] pathspec 提交（docs(final-rc) + artifacts）

### STOP B — Failure Family Classification

- [ ] 104 例（以本轮实测为准）按 A-O 族归类，矩阵含 单跑/组跑/全量 三列
- [ ] TEST_DEBT_UNCLASSIFIED=0
- [ ] Action 只允许：FIX_TEST / FIX_FIXTURE / FIX_ISOLATION / FIX_ENVIRONMENT / FIX_PRODUCTION / REMOVE_OBSOLETE_TEST / QUARANTINE_WITH_HARD_EVIDENCE

### STOP C — 高容量族逐族清偿（每族单独 commit）

- [ ] 候选优先序（据 §2.3 分布）：tools 族（29F）→ rag 上传/索引族（14F）→ api 契约族（11F）→ cs 族（6F）→ 其余
- [ ] 每族 `pytest <family> -q` 归零 + 近邻套件无新回归

### STOP D — 隔离/环境/Flaky 收口

- [ ] 已知 2 flaky（test_state_transition / test_metadata_shadow）：不能以「现在没复现」结案，按稳定性跑法取证（关键 flaky ×20 / 相关文件 ×10 / 目录 ×5）
- [ ] travel/api 挂死测试根因归类（connect_timeout 缺失属 FIX_ISOLATION/FIX_ENVIRONMENT，禁止用提高 timeout 掩盖）
- [ ] KNOWN_FLAKY_COUNT=0 / ORDER_DEPENDENT_FAILURES=0

### STOP E — Full Regression Closure

- [ ] 同口径全量（分块并集口径 or 整跑）FAILED=0 / ERRORS=0
- [ ] BASELINE_COLLECTED / FINAL_COLLECTED / REMOVED_TEST_COUNT / NEW_SKIP_COUNT / NEW_XFAIL_COUNT 对账
- [ ] NEW_REGRESSION_COUNT=0

### STOP F — Internal Closure Reopen

- [ ] 核心 smoke/contract 套件复跑（Authorization/SQL/RAG/Memory/Context Budget/Async/Idempotency/STOP E/SSE Resume/CS/Travel/Model Governance/Domain Runtime）
- [ ] 六 Gate 重判：GLOBAL_REGRESSION_PASS / PROJECT_INTERNAL_CLOSURE_PASS 如实落笔
- [ ] 最终报告 `docs/2026-09-25-FinalRC-TestDebt-Closure.md`
- [ ] External Gate 保持不动：PRODUCTION_RELEASE_GATE_PASS=false / EXTERNAL_BLOCKERS_PRESENT=true / FULL_ROADMAP_COMPLETE=false（API Key 轮换已完成除外）

---

## 6. 经验教训（供后续会话直接引用）

1. **口径三件套必须写死在命令里**：`PYTHONPATH=backend PGPORT=5433 PYTHONUTF8=1`（详见 `artifacts/final_rc/BASELINE_RUNBOOK.md`）。任何一次「直接 pytest」都会被双 tests/ 目录和双 PG 陷阱咬。
2. **宿主机整跑全量在当前容器栈规模下不可行**（DB 密集段挂死），分块跑 + 停滞看护是当前唯一稳定路径；STOP E 若需整跑口径，先解决 psycopg connect_timeout 缺失或 PG 连接余量问题。
3. **确定性挂死 ≠ 环境偶发**：同位置、同进度点数的三连挂死是「特定测试 + 无超时连接」的确定性组合，二分可定位，不能用 rerun 掩盖。
4. **Windows 驱动脚本**：GNU timeout 信号无效；杀树必须按 cmdline（`pytest|exec.eval`）而非进程名；先杀驱动循环再杀工作进程，否则「杀不死」。
5. **失败清单必须甄别环境污染再定性**：`too many clients / Connection refused` 特征 = 候选环境故障，小批量 `-n 1` 复跑绿即剔除；严禁把环境故障计成测试债，也严禁把真测试债洗成环境故障。
6. **PG max_connections=100 余量仅 ~48**，4 worker 测试风暴必然周期性打满——这是环境容量事实，暂不改生产配置（改 PG 启动参数属动共享容器，影响面大，本轮未授权）。

---

## 7. 对最终判定的影响（如实预判）

- 即使全部块跑通，**travel 块挂死根因未定位前 STOP A 不能判 PASS**。
- 上轮 105F/3E 与本轮分块清单的对账可能出现口径性差异（xdist worker 分布不同导致顺序相关失败的形态差），STOP B 分类时须以「单跑复现」为最终定性依据，避免把 xdist 形态差误判为回归或测试债。
- PRODUCTION_CODE_CHANGE_COUNT 到本报告时刻为 **0**，预期全程保持 0（除非出现硬证据的生产缺陷）。

---

## 8. 继续执行增量证据（2026-09-25）

### 8.1 已落库的最小修复与定向证据

| 提交 | 范围 | 证据 |
|---|---|---|
| `b2077e9` | `task_service` 数据库连接显式使用 `DB_CONNECT_TIMEOUT` | `backend/tests/test_index_task_runtime.py`：13 passed（单进程，542.99s） |
| `d932a40` | 旅游测试统一使用租户/用户隔离 thread id | `backend/tests/travel/test_run_context.py`：12 passed（单进程，11.04s） |
| `ed4f828` | LLM 动态 registry 与错误包装断言对齐现行契约 | 目标测试：17 passed（9.71s） |
| `52a9b69` | MiniMax 默认端点保留、模型角色契约与 provider 断言对齐 | infra 目标测试：20 passed（11.92s） |

此外，针对幂等 ledger 与邮件测试复现的 PG 半开连接等待，已在工作区加入同类 `connect_timeout` 边界及测试隔离修复；对应串行回归正在运行，尚未计入通过数，需待汇总后再提交。

前序串行数据库/幂等/邮件/STOP-E/G4 组合回归随后完成：`68 passed in 2697.34s (0:44:57)`，`RC=0`。该结果证明连接超时边界在该组长耗时用例中未引入失败；它仍只是定向证据，不等价于全量回归归零。

### 8.2 当前判定（不提前结案）

```text
CURRENT_HEAD=52a9b69（工作区另有幂等/邮件增量，未提交）
PRODUCTION_CODE_CHANGE_COUNT=3  # task_service、minimax、shared/idempotency
STOP_A_PASS=false               # 分块并集、挂死普查、环境污染甄别仍未全部完成
GLOBAL_REGRESSION_PASS=false
PROJECT_INTERNAL_CLOSURE_PASS=false
PRODUCTION_RELEASE_GATE_PASS=false
EXTERNAL_BLOCKERS_PRESENT=true
FULL_ROADMAP_COMPLETE=false
```

历史分块 `chunks/summary.txt` 曾被并发驱动交错写入，不能直接作为最终 census；后续只采信同口径、单进程或已明确隔离的有效日志，并在 STOP E 前重新生成完整并集与差集。

---

## 9. STOP A 收官（2026-09-26 凌晨，本会话增量——与前文 §1-§7 快照并读）

> 前言：§1-§7 为 09-25 白天的进行时快照；§8 为并行会话的增量判定；本节为 STOP A 最终收官。
> 三段并存，时间序如实。**多会话并发事实已并入判定**（详见 §9.4）。

### 9.1 分块并集已闭合（7397 collected 全执行）

- 全仓权威 collected = **7397**（PYTHONPATH=backend，独立 collect-only）。
- 15 个块（含 2 次拆分与容器内 booking）+ 27 条漏网补跑 = 全覆盖闭合。
- 漏网定位：块字母分区漏 `test_j*/k*/o*` 与 `scripts/` 目录（27 passed 全绿补跑）。
- 双归属：`test_sql_user_context.py` 被 sql 块与 root-c 各跑一次（29 条×2，去重计入）。

### 9.2 官方 census 数字（node 去重口径）

```text
BASELINE_COLLECTED=7397
BASELINE_RAW=174（169 FAILED + 5 ERROR，node 去重）
PERSISTENT_DEBT=89      ← 6 批 node 级 -n 2 受控复跑稳定失败（discriminate/batch*.log）
ENV_FLAKY_RECOVERED=85  ← 复跑复绿；其中 24 条另有 -n 1 串行全绿硬证明
恒等式：174 = 89 + 85 ✓
清单：baseline-failures-persistent.txt / baseline-envflaky-recovered.txt（artifacts/final_rc/）
```

### 9.3 环境病态与稳定跑法（零生产代码改动达成）

- 根因：宿主→docker PG 的 vpnkit 链路挂死（psycopg waiting 无限等）+ max_connections=100
  连接风暴；PGCONNECT_TIMEOUT 对 psycopg3 waiting 路径实证无效。
- 处置全部为**运行方式级**：DB 密集文件 `-n 1` 串行（68 passed 硬证明）；
  booking 族走容器内第二路径（43 passed 25.4s，宿主同段挂死两次）。
- 全部口径与跑法沉淀：`artifacts/final_rc/BASELINE_RUNBOOK.md`（v3）。

### 9.4 多会话并发声明（如实）

1. 本会话分块跑横跨 18:20-18:48 的 4 个提交（b2077e9→52a9b69）：16:01-17:31 完成的块
   在 ef85ba8 代码上，17:49 之后的块/甄别批在 52a9b69+工作区增量上。
   **persistent 89 条全部取得于最新代码状态（甄别批均在 21:00 后执行），清单有效**；
   §4 分块表的 raw 数字混合两态，仅作 census 参考，不做代码归因。
2. 检测到并行会话的串行回归进程与本会话甄别批存在时间重叠（批2 变慢、批3 三跑漂移
   14→11→10、一次日志文件交错写均发生在重叠窗）。按并发纪律，本会话自此**停发一切
   pytest**，不再与其竞争。
3. `PRODUCTION_CODE_CHANGE_COUNT` 口径修正：并行会话已落库 3 处生产修复
   （task_service 连接超时 / minimax 端点 / shared/idempotency 在途），**非本会话所为，
   本会话保持 PRODUCTION_CODE_CHANGE_COUNT=0**。
4. STOP B 的逐条单跑复核必须待并行会话回归收尾后再启动（并发冻结纪律）。

### 9.5 STOP A 判定

```text
FINAL_RC_STOP_A_PASS=true
（census 闭合 + persistent/env_flaky 清单落盘 + 上轮对账 + 并发事实并入）
GLOBAL_REGRESSION_PASS=false（本轮总目标，待 STOP B-F）
TEST_DEBT_UNCLASSIFIED=0 的归类工作 = STOP B，待并发窗口清空后启动
```

## 10. STOP B 增量复核（2026-09-26，本会话）

本节只记录本会话在 STOP A 收官后的增量证据；不回写 §1-§9 的历史快照，
也不把定向回归结果冒充全量回归。

### 10.1 已复核并收敛的失败族

以下族均在同一口径（`PYTHONPATH=backend`、`PGPORT=5433`、单进程、`--no-cov`）
下完成定向复跑，当前结果为全绿：

| 族 | 证据 |
|---|---|
| RAG 评估/上传/置信度/优化/文档注册/进度 | `105 passed`，163.04s |
| RAG pgvector 重建 | `6 passed`，47.42s；提交 `f87b01e` |
| SQL/CS/API/remote RAG/rerank/chat 契约 | 修复后目标用例全绿；提交 `da7949b` |
| 幂等、邮件、SQL Tool、选择理由/自纠、离线评测、LLM lazy proxy | 前序定向组合已全绿；提交链见 `git log` |

本轮新增的生产/契约修复包括：pgvector 元数据 DDL 按表名缓存、投诉 handoff
非映射桩安全回退、remote RAG `user_id/tenant_id` 签名透传、rerank 旧 binding
兼容、演示商品数据下 SQL 基线筛选、聊天输入上限测试改为读取统一配置。

### 10.2 仍未闭合：任务恢复族（环境污染证据）

`backend/tests/test_task_resume.py::test_resume_paused_continues_from_checkpoint`
单独运行仍耗时约 120s 后失败，错误为：

```text
IllegalTaskTransition: FAILED → RUNNING
```

同一测试的任务行在 60s 阈值后被宿主常驻 `agent-beat-1` 的
`tasks.pending_recovery` 扫描并投递到 `agent-agent-worker-1`；worker 不在 pytest
进程内，无法看到该用例的授权 monkeypatch，随后将测试任务写成授权失败。该证据与
此前批量复跑的 `test_task_admission_runtime`、`test_task_orchestration`、
`test_task_phase2_recovery`、`test_task_resume` 失败形态一致，属于
`FIX_ENVIRONMENT`（需独占 beat/worker 或测试专用队列/表），不能计为生产状态机回归。

本会话未停止共享容器，也未修改任务状态机；在没有独占维护进程的条件下，任务族
不能签发“全绿”结论。若要完成该族复判，必须先按 `AGENTS.md` 的容器纪律取得
停用/隔离常驻 beat 与 worker 的明确授权，然后单进程复跑该四个模块。

### 10.3 当前判定

```text
STOP_B_CLASSIFICATION=PARTIAL
NON_TASK_TARGETED_REGRESSION_PASS=true
TASK_RUNTIME_ENV_ISOLATION_REQUIRED=true
GLOBAL_REGRESSION_PASS=false
PROJECT_INTERNAL_CLOSURE_PASS=false
PRODUCTION_RELEASE_GATE_PASS=false
EXTERNAL_BLOCKERS_PRESENT=true
FULL_ROADMAP_COMPLETE=false
```

补充校验：remote RAG/CS/rerank/embedding guard 组合 `62 passed`（57.11s）；
随后修正 identity 字段只进入 `ask` payload，提交 `816198f`。

任务族补充证据：8 个历史失败节点在隔离库 `agent_memory_rc_test` 串行为
`6 passed / 2 failed`；剩余两项均为宿主机 vpnkit 续租阻塞窗口内的超时形态。
同一源码在 Docker 内网直连 PostgreSQL/Redis 的最小真实探针输出
`HEARTBEAT_PROBES_OK`（admission token 续期、lease takeover 两项均通过），
故任务生产状态机不改，剩余两项归类为 `FIX_ENVIRONMENT`。该临时测试库未触碰
业务库，保留供后续复判。

本会话提交：`f87b01e`、`da7949b`、`816198f`。工作区内 `data/**` 为测试/其他会话生成的既有
改动，未纳入本次提交。
