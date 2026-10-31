# PROGRESS — ci-regression-debt

> 记录证据、阻塞和下一步。每消化一批即更新。

## 2026-10-30 开工

- 环境核对：本地 main = origin/main = 2517e98（≥ b4c881f）✓
- 工作树：`agent-wt-ci-debt`（分支 fix/ci-debt-hangup，自 origin/main 2517e98 创建），.env 已从主仓库复制。
- 任务文档建立。

## 专项一：api/rag 挂死用例

### 关键转折：api suite 不是挂死问题（2026-10-30）

- 命令：`D:/Python/python.exe -m pytest backend/tests/api --timeout=150 -n 4 --dist loadfile -q --no-cov`（worktree 内，输出 /d/tmp/ci-debt/api_run1.txt）
- 结果：**59 failed / 816 passed / 0 Timeout，10.5 分钟跑完**。150s 超时一次未触发——api 在本地无挂死用例，此前 CI 被杀是收集崩溃（已根修）与失败用例耗时的历史叠加，不是持续挂死。
- 基线失败清单复核：GitHub artifacts 重新下载（本地 d:/tmp/regmap 的 zip 全部损坏不可用），11 个清单合计 108 行与基线 v1 一致；`failures-api` 为空（被杀前未落盘）。
- **59 个失败全部定性为测试资产漂移**（重构后测试未同步），非产品 bug：
  - **A 组 travel V1 退役（35 例）**：b178338 把全部 V1 端点搬进 `_retired_v1_router` 且无处挂载（`tests/travel_v2/test_legacy_persistence_retired.py` 显式守卫「不得挂载」）；V1 函数路径因账本退役 fail-closed 必失败。candidates/plan_versions 两个文件整文件测退役端点 → 删除（git rm）；auth/trace/checkpoint/request_execution 迁移到 V2 入口（`travel_v2_plan_stream`），并补 V2 stream 的 401 边界用例。
  - **B 组 rbac 分离（16 例）**：9d7c582「客服与部门管理员分离」重构未同步任何后端测试。users 列表字段契约变化（去 csAgent 加 email）；cs 档案字段从 users PATCH 拆到 PATCH `/customer-service/team/{id}`（会话吊销语义完整保留，测试迁移后继续覆盖）；GET /users 改 `require_rbac_access`（平台管理员 OR 部门主管）；users 详情去 csRoles；departments 表新增 department_type/manager_user_id 与 _change_department 重写。修复 = fake 替身扩列 + 用例迁新端点 + 断言对齐新契约。
  - **C 组其他（8 例）**：trace_children → get_trace 资源级授权迁移（身份打桩 + list_children 带 tenant_id）；selection → PUT /weights 加 require_admin_user（测试补 admin 身份头）；cs_pending → 投影新增 reason 字段（补断言）；automations scan → **单跑通过，xdist 共享 DB 竞争，归入专项三**。

### 批次验证记录

- 批 1 travel：test_travel_auth(9) + trace(2) + checkpoint(2) + request_execution(10) = 23 passed；删除 test_travel_candidates_api.py、test_travel_plan_versions_api.py。
- 批 2 rbac：test_rbac_api.py 33 passed、department_management 6 passed、user_lifecycle 端到端 passed、auth_session_family 端到端 passed。
- 批 3：trace_children 2 passed、selection 10 passed、cs_pending 5 passed。
- api 全套第二轮回归：RUNNING（api_run2.txt）

### 已知实证（勿重走弯路）

- 单 job 一次性全量跑不完（挂死用例排队超 180min，进程被杀失败清单也不落盘）——只能 matrix 按 suite 跑。
- 本地全量跑到 ~46% 会挂死——本机 D:/Python 已装 pytest-timeout，测试务必带 `--timeout=150`。
- rag/api 在 50min job 上限下跑不完——api 修复合入后 dispatch suites=api 验证即可判定。
- CI artifacts 的本地 zip 副本易损坏，一律 `gh run download` 现取。

## 阻塞

（无）

## 下一步

1. api 第二轮本地回归 → 剩余失败复核（预期仅 automations 并发敏感 1 例）→ 提 PR。
2. PR 合并后 `HTTPS_PROXY=http://127.0.0.1:7897 gh workflow run full_regression.yml -f suites=api` 验证 CI 收敛。
3. 专项二：demo 库环境补齐——`DC_DATABASE_URL` 默认 `localhost:5432/demo`（backend/config/data_collection.py:29），CI workflow「Initialize isolated databases」步加 CREATE DATABASE demo + 初始化（预期消化 data_collection 12 例 + sql-infra 部分用例）。
4. 专项三：并发敏感（orchestration/travel/customer_service ~41 + misc inventory 10 + automations scan）。
5. rag 基线补取。

## 2026-10-30 晚：CI 验证收官 + 隔离基建第一批

### CI 实证收敛（main 分 suite 失败数，基线 v1 → 当前）

| suite | 基线 v1 | 当前 CI 实证 | 修复手段 |
|---|---|---|---|
| api | （被杀无清单） | **0**（两次 dispatch job success） | #139 测试资产对齐 59 例 |
| tools-security-obs | 16 | **0**（run 41114061156） | #140 demo 库（12）+ #144 no_io 口径（1）+ 3 例随基线自然消化 |
| sql-infra | 14 | **0**（run 41114061156） | #141 只读角色（12）+ #144 fallback/billing（2） |
| orchestration | 24 | 见下方 in-flight | #144 真失败 15 例 + #145 env 13 例 + B 组 7 例隔离待定 |
| misc | 22 | 部分随环境修复消化 | inventory 10 例 xdist 竞争 → #145 隔离基建 |
| 其余 7 suite | 25 合计 | 0（各 dispatch 均 success） | — |
| rag | （两轮被杀） | 首次跑完 => success（run 41093726731 起） | 挂死未复现，基线自然取得 |

- #145（WORKFLOW_DB_PGDATABASE + pgtest worker 隔离 + automations tenant 后缀）合入后的 orchestration 验证 run 41119049979 进行中。
- #144 squash 合并竞态记录：REST PUT merge 在 push 后立即调用会拿到旧 head（e57c3e6 未进 33335d5），导致 env 修复漏合一次——续工时合并 PR 前先 `gh pr view --json headRefOid` 核对。

### 隔离基建第一批（#145）

- `worker_scoped_prefix()`：pgtest 表名按 `PYTEST_XDIST_WORKER` 分名，DROP 收窄本 worker（修 CREATE TYPE pg_type 冲突 + teardown 互删）。
- automations 测试租户/用户带 worker 后缀（共享真实表 `ai.user_automations`）。
- B 组遗留：router_understanding/cs_context 7 例 xdist 顺序敏感（单跑全过），待第二批隔离排查。

### 主栈部署验证

- main = 33335d5+#145 后 `devctl.bat rebuild` 完成，全部容器 healthy（reporter.py 改动随 #144 进容器）。
- chat 链路 sql_empty 追问功能的容器级 E2E 未单独执行（单测 43+20 passed 覆盖行为）；如需 E2E 验证另行安排。

### 下一步

1. orchestration run 41119049979 结果 → B 组 7 例与偶发（market_research 降级）定性收口。
2. 隔离第二批：router_understanding 共享状态定位（cs_context KeyError 的全局单例污染源）。
3. 全量 dispatch（不带 suites 参数）取基线 v2 全景，核对每周失败数下降曲线。

## 2026-10-31 凌晨：全量基线 v2（部分）

Run 41120872440（main = #146 后）。**api/rag/backend-root 三 job 从未启动即 cancelled**（疑 runner 并发/额度，started_at 为空）——api 两次 dispatch 已实证 0 失败，rag 已实证能跑完；三 suite 下周例行跑或手动 dispatch 补取。

### 分 suite 现状（基线 v1 → v2）

| suite | v1 | v2 | 剩余失败构成 |
|---|---|---|---|
| api | 杀 | **0**（两次 dispatch 实证） | — |
| tools-security-obs | 16 | **0** | — |
| sql-infra | 14 | **0** | — |
| orchestration | 24 | **7** | B 组 router_understanding/cs_context 6 例 + selection 端到端 1 例，全部 xdist 顺序敏感（单跑全过） |
| misc | 22 | 22 | inventory 10（xdist 竞争，隔离第二批）+ kafka 模块缺失 2 + skills 报错文案 3 + domain/audit 真回归候选 4 + 其他环境 3 |
| travel | 13 | 13 | booking 并发/幂等（xdist）+ provider 契约 + decision audit |
| services-travelv2 | 10 | 10 | 待定性 |
| customer_service | 4 | 4 | 幂等竞争/状态时序 |
| prompts-context | 2 | 2 | 待定性 |
| evaluation | 2 | 2 | 待定性 |
| repo-tests | 1 | 1 | 待定性 |
| rag / backend-root | 杀/0 | cancelled 未跑 | 例行补取 |

**净改善：108 → 61（-47），其中三 suite 清零。**

### 下一步（优先级序）

1. **隔离第二批**：xdist 竞争是剩余失败的最大公约数（misc inventory 10 + orchestration 7 + travel booking 6 + cs 4）——B 组 cs_context 的全局单例污染源定位是钥匙（router_node/understanding 链路的模块级状态）。
2. **misc 真回归候选**：inventory notification 10 例（策略匹配语义）单跑即绿还是真漂移需在干净环境确认；kafka 测试补模块或 skipif 标注；skills 报错文案 3 例对齐新格式。
3. **travel/travelv2/cs/prompts/evaluation/repo-tests 定性**：逐个单跑→xdist 对比，真失败修复（同 api 批方法论）。
4. 每周例行跑失败数曲线：v2 起 tools-security-obs/sql-infra/api 三 suite 应稳定为 0，作为回归门禁观测点。

---

# 交接（2026-10-31，供下一会话续工）

## 工作树与环境

- **续工工作树**：`D:/Program Files/workplace/agent-wt-ci-debt2`（任务专用，保留）。当前分支 `fix/ci-debt-worker-isolation` 已合并（=main），续工时从这里 `git fetch && git checkout -b fix/ci-debt-<批次名> origin/main` 开新分支。
- `.env` 已从主仓库复制（gitignore，测试连库必需）。
- 主仓库 `D:/Program Files/workplace/agent`（main）供 rebuild/部署，`tmp/*` 有历史未提交删除不要动；`agent-wt-ci-debt` 旧工作树已清理。
- 本地资产：`d:/tmp/ci-debt/`（基线 v1/v2 失败清单、orchestration/automations 本地跑输出、探针日志）——可能被清理，权威失败清单以 GitHub artifacts 为准（`gh run download <run_id> -n failures-<suite>`）。
- 本地验证命令模板：`D:/Python/python.exe -m pytest backend/tests/<dir> --timeout=150 -n 4 --dist loadfile -q --no-cov`（本机 D:/Python 已装 pytest-timeout，**必带 timeout**）；本机 PG 5433（主栈容器），CI 是 5432。

## 坑位（本会话实证，勿重走）

1. **本地共享栈 worker 干扰**：主栈 celery worker 容器（agent-agent-worker-1 等）会拾取共享 Redis 里测试任务真执行 → tasks_api/automations 各 1 例偶发失败、automations 全文件跑 9 分钟。单跑失败先判断是否此噪音（CI 无 worker）。**不是产品 bug，不要修 main**。
2. **squash 合并竞态**：push 后立即 REST PUT merge 会拿到旧 head（#144 的 env 修复因此漏合一次，#145 补的）。合并前先 `gh pr view <n> --json headRefOid` 核对。
3. **gh/网络抖动频繁**：TLS handshake timeout / 502 / EOF 是常态，重试即可；同分支第二个 PR（如 #144、#146）会报 "no checks reported" 且可能报 merge conflicts（与已 squash 的同区改动冲突）——本地 merge origin/main 解冲突后 push 再合。
4. **同分支先后 PR**：#140/#141 同分支（fix/ci-debt-demo-db），#140 squash 合并后 #141 必然冲突——要么每 PR 独立分支，要么按 #141 的处理流程解决。
5. **主栈红线**：不动 agent_readonly 角色密码、不动主栈数据；本地跑需要只读角色的测试用 `.env` 里已有配置。
6. **测试资产漂移是主旋律**：本会话 59+15+5 例失败里无一需要"修产品 bug"（除 reporter sql_empty 一处真回归）；先 `git log -S` 找引入提交定性，再决定改测试还是改实现。

## 恢复步骤

1. `cd /d/Program Files/workplace/agent-wt-ci-debt2 && git fetch && git checkout -b fix/ci-debt-<批次> origin/main`
2. 按上面 TASK.md「剩余计划」顺序开工（专项三 A 隔离最优先）。
3. 每修一批：本地对应文件跑绿 → PR 合 main → `HTTPS_PROXY=http://127.0.0.1:7897 gh workflow run full_regression.yml -f suites=<name>` 验证 → 台账 +1 行。
4. 修 backend 产品代码时：主仓库 `git pull` 后 `echo y | ./devctl.bat rebuild`（Git Bash 管道喂 y），确认容器 healthy。

## 当前进度锚点

- main 含全部修复（≥ a7a5471/#149 后的 commit）。
- v2 分 suite 失败数见上方对比表；剩余 61 例清单：`/d/tmp/ci-debt/v2full/`（8 suite）+ orchestration 7 例见 `/d/tmp/ci-debt/v2/failures-orch4/`。
- 全量 run 41120872440 的 api/rag/backend-root 三 job 被取消（未启动），需补取。

---

# 第二批会话（2026-10-31 晚）：v2 剩余 61 例全部定性修复

## 分批实证

### 批一 PR #153（orchestration 7 + misc inventory 10）

- **orchestration 7 例定性推翻「xdist 顺序敏感」**：本地全目录 xdist 1071 passed 复现不了。真因 = **CI 环境差异下的测试资产漂移**：
  1. CI 无 .env，`CS_ENABLED`/`TRAVEL_ENABLED` 走 sys_config 注册表缺省 **false** → `try_cs_prefilter`/`try_travel_prefilter` 恒降级（KeyError cs_context / route_mode=plan）。原 fixture patch `config.CS_ENABLED` 在 M16 后**无消费方**（漂移残留）。修：`monkeypatch.setitem(sys_config._values, ...)` 锁唯一判定源。
  2. selection 端到端 1 例真实异常是 **KeyError: 'tasks'**（交接文档记的 "assert 503 == 200" 是笔误）：`GET /selection-decision/tasks` 未带头 → router 级 `resolve_operator_role` → `require_internal_token` fail-closed 503 → error 壳无 tasks 键。修：与 POST 同口径补 `_auth` 头。
- **inventory 10 例**：CI 建有无后缀 `agent_business` 兼容库（004 迁移）且 env 未设 `INVENTORY_DB_PGDATABASE` → inventory 测试表落进该库，`drop_pgtest_tables` 只扫 BUSINESS/WORKFLOW 配置**永远清不掉** → 同 worker 前序用例行毒死后续。修：workflow env 补 `INVENTORY_DB_PGDATABASE=agent_business_test`（#145 同款）+ pg_env drop 列表补 `INVENTORY_DB_PG_CONFIG`（跨库清理行为验证 PASS）。**CI 实证：run 41138708986 orchestration/misc 清零（inventory 10 例不再出现）**。

### 批二 PR #154（misc 12）

- business_report 1：`/report-files/*` 在 API-Key skip 白名单内 200；穿越路径落出白名单 → CI 无 .env 时 fail-closed 503（本地 401）。测试进程内注入 API_KEY，**断言不放宽**。
- kafka 2：kafka-python pyproject 已声明但 **requirements-lock.txt 遗漏**（CI 安装源是 lock）。按 2026-10-29 opencv-python 先例定向补入 2.0.2。**CI 实证：run 41142354024 pytest misc success（misc 22→0）**。
- skills 3：b6b7973 校验收敛到 Governance Runtime JSON Schema（文案带路径、int→integer、根 additionalProperties=False 拦未声明键）——对齐新契约；`test_undeclared_keys_not_blocked` → `test_undeclared_keys_blocked_by_strict_root`。
- tool_runtime 3：7e64152 文件系统 PermissionError 类型判定优先（infra_error），账号无权走文本路径——拆两用例分别锁定；tool lock 生成器再生成补 memory_forget_tool 中文名；admin_tools 守卫名 require_admin_user → require_editor_user（rbac 分离）同步打桩点。
- domain_runtime 2：domain_flags fixture M16 同款漂移改 sys_config setitem；d18 前置 Handoff 读取（6f12d46）要求 tenant/conversation 键 + mock PG。

### 批三 PR #158（travel 13 + cs 4 + prompts/evaluation/repo-tests 5 + summary）

- decision_audit 4：**b73ed86 起 V1 决策留痕退役**（`_ensure_table` 恒 False），record/list 仅被 `_retired_v1_router`（无处挂载）调用——整链死代码，按 #139 先例删除测试文件。
- booking 5：四表共享 travel schema + ledger 表名固定，xdist worker 间 TRUNCATE/DROP 互踩（in_doubt 实证）。conftest **会话级 advisory lock 跨 worker 串行化**（修复前 xdist 5 failed → 后 48 passed）。
- event_stream 1：V1 端点 404 → 迁 `/travel/v2/plan/stream`。
- cs d18：**store 现契约 = 每 (user, session) 一条当前确认**（save 整行覆盖，STOP D 升级语义）——原四 action_id 构造下 C 的保存复用覆盖 B 的行、D 被拦（DB 行级实证，conf-B 行落库后变 return_request）。重写为跨会话重发（TERMINAL_BLOCKED_ACTIONS 空集，success 后放行）。
- cs cs_experts：290d2b7 起 refund_request 走专用真实链路（fail-closed），不再消费 `_simulate_execute`——用例改 address_update 保持意图。
- cs p5：本地 xdist 1495 passed 未复现（CI runner 时序竞争）——**留 v3 观察**。
- context_budget auto_compact：fixture 12 轮太短，重建后固定 policy 开销反超压缩收益（275 vs 251）→ 30 轮。
- evaluation 2：37ae046 temperature 0.1→0；8a97a63 数据集版本 candidate.4（loader 对齐 manifest；suite 文件冻结声明 candidate.1 是另一事实源，升版属 DATA-01 流程，未扩范围）。
- repo-tests 1：a9184e4 同 request_id 重启流 409（stream_registry 进程级单例跨用例存活）→ ASCII 用例独立 id。
- **workflow Regression summary 恒红**（全部历史 run 实证，含全量 v2）：`toJSON(needs.*)` 单依赖时序列化为数组 → 形状兼容修复。**此修复是「suite 全绿 = run 绿」验收的前提**。
- 本地总回归：五目录 xdist **3570 passed**。

## 本会话新坑位（勿重走）

7. **CI 环境差异优先于 xdist 猜测**：v2 的「xdist 顺序敏感」预判大半是错的——本地复现不了时先 diff CI workflow env 与本地 .env（域开关、认证豁免、INVENTORY 库名），再谈共享状态。模拟 CI env 方法：命令行显式设 `CS_ENABLED=false TRAVEL_ENABLED=false ALLOW_UNAUTHENTICATED= AI_INTERNAL_TOKEN= pytest ...`（load_dotenv 不覆盖已有 env）。
8. **本地 PGHOST 必须用 127.0.0.1**：localhost 先试 ::1 被拒再回退 IPv4，psycopg 每次连接恒 ~5s，booking 类高频连库测试直接超时。`PGHOST=127.0.0.1` 连接 0.05s。
9. **fixture patch 目标要对准唯一判定源**：M16 后域开关走 sys_config（`_values` dict），patch config 模块属性无效；业务守卫改过名（require_admin_user → require_editor_user）后 patch 旧名等于没拦——**每次 patch 前先确认实现在读哪个名字**。
10. **测试构造前先读 store 语义**：cs confirmation 是「每会话一条当前确认」（save 覆盖），按多行构造必翻车——DB 行级实证比源码推理快。
11. **advisory lock 串行化是共享表 xdist 互踩的通用解**：store schema/env 硬编码无法 per-worker 建表时，fixture 持会话级 advisory lock，setup/teardown 与用例体的互踩窗口被锁消除（booking 实证）。
12. **gh 需要 git 仓库上下文**：`gh run download` 在 /d/tmp 下报 "not a git repository"，必须在 worktree 内执行；artifact 与 CI 日志的 FAILED 行可能不同（artifact=断言行，summary=最终异常类型），以 CI 日志 short summary 为准。

## 恢复步骤（下一会话）

1. `cd /d/Program Files/workplace/agent-wt-ci-debt2 && git fetch && git checkout -b fix/ci-debt-<批次> origin/main`。
2. **核对全量 v3（run 41151145995）结果**：预期全部 suite 0 失败（p5 1 例与 provider_contracts 2 例、collapse 1 例属低概率时序竞争，若复现按本文件坑位 7 的方法重定性）。
3. 若 v3 有残余：按批三方法（单跑→xdist→CI 模拟 env 三步定性）继续。
4. 每周例行跑失败数曲线观测：v3 起全部 suite 应稳定 0。

## 当前进度锚点

- main 含全部修复（≥ d658387/#158 后的 commit）。
- v2 61 例定性总表见 [TASK.md](TASK.md)；三批 PR：#153 / #154 / #158。
- 全量 v3：run 41151145995（2026-10-31T00:37Z dispatch，无参数全量）。
- 本地资产：`/d/tmp/ci-debt/`（v2orch、v3misc、orch_ci_v2.log、travel_xdist_fix1.txt、cs_xdist1.txt、pc_xdist1.txt、batch3_regress.txt 等）。

## v3 实证 + 批四（PR #160）+ v4 收敛（2026-10-31 深夜）

### 全量 v3（run 41151145995，main = #158 后）

- **20 失败 / 12+1 jobs**：misc、orchestration、evaluation、tools-security-obs 清零；Regression summary 首次变绿（修复生效）。
- 残余定性：travel 4（d2_two_hop 2 + provider_contracts 2，域门/Key 门 CI 缺省关）、sql-infra 1（#157 的 schema 级 GRANT 把 125 新表授给 agent_readonly，守卫按设计拦）、repo-tests 1（125 漏 rag_smoke 分类登记）、collapse 1 + p5 1（CI-only，本轮 dispatch 已过——间歇性）、services-travelv2 10（probe 8 + memory 2）、api/rag/backend-root 又被取消（runner 并发，老问题）。

### 批四 PR #160（13 例）

- provider_probe 8：url_guard SSRF 预检真 DNS 解析测试域名 → catalog 契约用例替身 assert_url_allowed（**仅 test_catalog_* 且排除 rejects_private**——第一版全局 autouse 误伤 3 个 SSRF 行为用例，'pass'=='fail' 实证后收窄）。
- d2_two_hop 2：TRAVEL_BOOKING_ENABLED=false 本地模拟与 CI 失败逐行一致 → autouse 开闸（booking 判定源 = config 模块属性，evaluation runner 同口径）。
- provider_contracts 2：merchant 门 `MAP.is_amap_configured()`（CI 无 AMAP_KEY）、train 门 `MCP_CFG.is_train_mcp_enabled()`（不是 train_mod.TRAIN_MCP_ENABLED——patch 错位置报 AttributeError 才发现）。
- repo-tests 1：125_mall_wallet.sql 只登记了 init_db.py、漏 rag_smoke filter（#157 单边登记；守卫测试在 tests/ 根目录而非 backend/tests/）。
- sql-infra 1：CI init 的 schema 级 GRANT 在 init_db 之后执行 → 125 新 wallet 表被一并授权 → 守卫 fail-fast（**全量回归当门禁抓到并行会话的越权授权**）。修：CI init 显式 REVOKE（未纳管 fail-closed）。

### 验证 run 41205470228（#160 合并后，全 matrix 实跑）

- **12/13 suite 全绿 + Regression summary 绿**；唯一残余 services-travelv2 3 例（memory 2 + model_config_persistence 1，模型注册表共享状态家族，specialized 2 例本轮已过）。
- **收敛曲线：v1 108 → v2 61 → v3 20 → v4 3。**

### 本会话新增坑位（续）

13. **`git checkout -b <branch> origin/main` 前必须核对 fetch 是否真成功**：本会话台账分支创建时 fetch 网络抖动失败（tail -1 吞了报错），origin/main 停在旧点，checkout 把工作树**静默回退**——#158 全部修复（booking 锁、删除的文件、V2 迁移）在本地消失，批四编辑叠在旧树上跑出 7 个幻影失败（已删文件复活）。恢复：stash 编辑 → 核对 `git log --oneline -1 origin/main` → 重开分支 → stash pop。**分支创建后第一件事：`git log --oneline -1` 确认 parent 是预期 commit。**
14. **门替身要打在真正的判定函数上**：patch 旧属性名（train_mod.TRAIN_MCP_ENABLED 不存在）会直接 AttributeError 暴露；而 patch 全局 autouse 会误伤专门测守卫行为的用例（provider_probe 的 SSRF 用例）——用测试名条件收窄 + 显式排除名单。
15. **守卫测试可能在 tests/ 根目录而非 backend/tests/**（repo-tests suite）：找测试文件先 `find . -name` 别假设目录。
16. **并行会话的连带义务是双向的**：#157 加迁移只登记了 init_db 漏了 rag_smoke filter；加 finance 表触发了只读授权越权守卫。全量回归是抓这类跨会话漂移的唯一网——这正本任务存在的意义。

### 下一会话

- 唯一残余：services-travelv2 3 例（TASK.md「剩余计划」有根因方向），修完即 0。
- p5 / collapse 已间歇性转绿，无需再修；若未来复发按坑位 7 的 CI 模拟方法重定性。
- 每周例行跑：v4 起除 travelv2 外应稳定全绿。

## 交接定稿（2026-10-31 深夜）

残余 3 例的完整交接（总计划/进度锚点/工作树/测试/验收五节）已写入 [TASK.md](TASK.md) 文末「交接」章节，下一会话直接按该章节执行；本文件坑位 1–16 为配套避坑索引。

---

# 第三批会话（2026-10-31 深夜 ~ 2026-11-01）：批五~批十二，services-travelv2/api/cs/prompts 清零，backend-root 首曝移交

> 会话经历两次意外中断（关机/会话退出），三次接力完成。工作树一度被外部清理（非本会话操作），从 origin/main 重建后无实质损失（成果全部以 PR 形式固化）。

## 分批实证（run 号即证据）

### 批五 #168：api 13 例（v5 首曝）

- **v5（run 41211617846，#163 后首个真实全量）暴露 api 13 例**。定性：44d2e53 起 internal token 通道 fail-closed，CI 无 AI_INTERNAL_TOKEN → 无头请求先撞 503。修：按断言形态分流注入（6 例 `messaging.AI_INTERNAL_TOKEN`→401；5 例 `deps.ALLOW_UNAUTHENTICATED`→service 身份→enforce 403）+ 2 例 JWT_SECRET setenv。
- **重大定性修正**：v4 run 41205470228 的 api job 仅 16 秒——**gate skip 假象**，v4 并非「全 matrix 实跑」；api 自 #139 后从未真实全量实跑，v5 是漂移首曝（坑位 17）。
- 对照实验：284ef58（pre-#163）dispatch api 同样挂死 → api 问题与 #163 的 SECRETS_ENCRYPTION_KEY 无关。
- 验证：CI 模拟 env 11 passed；7 文件 90 passed。

### 批六 #171 + 批七 #172：cs 3 例 + prompts 1 例

- maintenance（v6 新面孔）：36f40b9 起 `run_sync` 契约改 `(alerts, closed)` 二元组，mock 桩停在旧单 list——契约漂移。
- **p5 根因实锤**（批六诊断断言 `scanned=0 contended=1` 一锤定音）：`config/redis.py` 的 **REDIS_ENABLED 缺省 false** → CI `get_redis()` 走静默 None 分支（无日志，解释零 warning 死角）→ presence fail-closed → reaper 放弃恢复。修：workflow 补 `REDIS_ENABLED=true`。**p5 的「间歇性竞争」定性推翻——真实全量中 100% 失败，是确定性环境缺失**。
- collapse（v9 诊断 `ratio=0.846 达标但 folds=0`）：CI 缺省 `LLM_CONTEXT_LENGTH=4096`（本地 .env 8192）→ budget=3072 → po/rag 占满后 **L2 先行裁剪吃掉折叠候选**（user 轮数 ≤ keep）。本地 4096 口径首次复现。修：用例锁定 8192 口径；**patch 目标必须是 `backend.config.llm` 子模块属性**（token_counter 函数内 from-import；patch config 包 re-export 绑定无效——坑位 19）。
- v9：cs 全绿；v10：prompts 绿。

### 批九 #176：诊断资产改造

v10 实证 cs「1518 passed 0 failed 但 2 errors」时 failures 清单为空（`-rf` 不列 error）且 pytest_out.txt 不在 artifact——**诊断无门**。修：`-rf`→`-ra` + artifact 补 pytest_out.txt。

### 批十 #179 / 批十一 #180：sla_graded 两层

- v11（`-ra` 首航）直接定位：`_pg()` 自行 `open(".env")` → CI 无 .env 文件 FileNotFoundError（批七 Redis 打通前降级路径掩盖）。修：MEMORY_DB_CONFIG 口径。
- 批十修通后 v12 暴露下一层：`close_stale` 按 `updated_at < cutoff` 关闭，`_seed` 只置 created_at → CI 无主栈 reaper 时 breach 行无人关闭；**本地绿是主栈 maintenance worker 并行兜底掩盖的**（docstring 自述）。修：_seed 补 updated_at 置 stale。
- v13：cs **success，清单空**。

### 批十二 #181：api 第二个 .env 依赖文件

v14 全量（11/13 绿）中 `-ra` 新清单暴露 `test_cs_agent_status_transfer.py` 全文件 FileNotFoundError——批十同款，已修（本地 10 passed）。api 定向确认在途。

### v14 首曝：backend-root 54 例（移交批十三）

该 suite v5/v6 均被 runner 杀掉**从未真实跑完**，v14 首次出完整失败清单。定性方向按文件分组写入 [TASK.md](TASK.md) 交接第三节（.env 依赖类 / REDIS 连带 / anti_ban 配额泄漏 / 逐例资产漂移 / pgtest 竞争）。

## 坑位（17–21，续接 1–16）

17. **gate skip 的秒级 success 勿当全量证据**：dispatch 定向 run 里未选中 suite 的 job 也显示 success（Suite pytest skipped），job 时长是识别标志（秒级=skip）。v4「12/13 全绿」实为 1 个 suite 真跑——「间歇性转绿」类定性若基于此全部作废，重看真实全量。
18. **诊断断言是 CI-only 疑难的高效武器**：对静默 continue / 触发判定类失败，先给断言消息携带分流数据（ReapResult 计数、used/budget/ratio），一次 dispatch 拿决定性证据再修——p5、collapse 两案各一次实验闭环，避免盲猜循环。注意属性名要对照 dataclass 定义（overflow 翻车一次）。
19. **from-import 的 patch 目标是子模块属性**：`from backend.config.llm import X` 在函数内执行读的是 `config.llm.X` 原模块属性；patch `backend.config` 包的 re-export 绑定无效。坑位 9 的变体，patch 前先看实现在读哪个命名空间。
20. **本地环境红利清单**（本地绿 ≠ CI 绿的系统性原因）：REDIS_ENABLED=true（主栈 Redis）、LLM_CONTEXT_LENGTH=8192、JWT_SECRET/AI_INTERNAL_TOKEN/SECRETS_ENCRYPTION_KEY（.env）、主栈 maintenance/dispatcher worker（reaper 兜底）、MEMORY_DB_CONFIG 指向真实库。CI 模拟 env（坑位 7）必须显式清空这些键。
21. **api/rag/backend-root 挂死形态**：Suite pytest 步骤 ~50min 无输出被 job 上限杀（v6/v10 两轮三 job 同挂，v5/v14 快败完成）——runner 层非确定性，pytest 输出重定向后无实时日志；批九已补 pytest_out.txt 进 artifact，被杀后如有部分输出可查。处置：重 dispatch（抽签），不阻塞其他 suite 结论。

## 收敛曲线（真实全量口径）

v1 108 → v2 61 → v3 20 → v4（skip 假象，实际仅 travelv2 3）→ **v5 15**（api 13 首曝 + p5/collapse）→ v6 4（api/rag/backend-root 挂死）→ v9 2 → v10 2 errors → v12 1 → v13 cs 0 → **v14 54+api（backend-root 首曝）**。修复节奏：#163/#168/#171/#172/#174/#176/#179/#180/#181。

## 下一会话

- api 定向确认（#181 后）→ backend-root 54 例（批十三，TASK.md 交接第三节有分组预判）→ 全量收官 COMPLETED。
- p5/collapse 类已绝杀：REDIS/口径根修后无残余；若未来复发按坑位 18 加诊断断言。
