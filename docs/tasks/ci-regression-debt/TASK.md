# CI 全量回归暗债清偿

> 任务 ID：ci-regression-debt
> 创建：2026-10-30；最近交接：2026-11-01（第三批会话，批五~批十二）
> 状态：IN_PROGRESS（**12/13 suite 定向实证全绿**；api 修复待 v15 定向确认；backend-root 首曝 54 例已登记移交——**续工直接按文末「交接（2026-11-01）」章节执行**）

## 目标

把每周 Full Regression（`.github/workflows/full_regression.yml`，13 suite matrix，约 12545 用例）的失败清单收敛到 0：逐周下降直至绿。

## 背景

- 基线 v1（run 40948443665，2026-10-29）：108 失败。
- **基线 v2（run 41120872440，2026-10-31 凌晨）：61 失败（净减 47），api / tools-security-obs / sql-infra / orchestration（dispatch 复验）清零**。
- 本会话（2026-10-31 晚）把 v2 剩余 61 例全部定性并修复（PR #153/#154/#158），全量 v3（run 41151145995）实证：**20 失败，misc / orchestration / evaluation / tools-security-obs 清零，Regression summary 修复生效**。
- 批四（PR #160）消化 v3 残余 13 例，**验证 run 41205470228（全 matrix 实跑）：12/13 suite 全绿 + Regression summary 绿，唯一残余 services-travelv2 3 例**。

各 suite 明细与逐批实证见 [PROGRESS.md](PROGRESS.md)。

## v2 61 例的定性总表（本会话完成，供复核）

定性分布：**xdist 竞争 2 类（booking 互踩 / p5 时序）+ CI 环境差异 3 类（域开关 env 缺失 / 认证 fail-closed / INVENTORY 库漂移）+ 测试资产漂移（对齐新契约约 20 例）+ 退役功能测试 4 例（删除）**。无一需要"修产品 bug"——与前序批次结论一致（测试资产漂移是主旋律）。

| suite | v2 失败 | 根因 | 修复 PR |
|---|---|---|---|
| orchestration | 7 | CI 无 .env：CS/TRAVEL 总闸 sys_config 缺省 false，fixture patch 的 config.CS_ENABLED 非 M16 判定源；selection GET 缺认证头（router 级 resolve_operator_role → fail-closed 503） | #153 |
| misc | 22 | inventory 10：CI env 未设 INVENTORY_DB_PGDATABASE，测试表落进无后缀兼容库且 drop 清理扫描不到 → 同 worker 行残留互踩；kafka 2：lock 漏包；skills 3：b6b7973 校验收敛契约；tool_runtime 3：7e64152 错误映射契约 / lock 过期 / rbac 守卫改名；domain_runtime 2：M16 同款漂移 + 权威 Handoff 前置键；business_report 1：API-Key fail-closed 503 | #153 + #154 |
| travel | 13 | decision_audit 4：V1 决策留痕退役（删除测试）；booking 5：xdist worker 互踩共享表（advisory lock 串行化）；event_stream 1：V1 端点 404 迁 V2；provider_contracts 2：xdist 类（本地 xdist 全绿） | #158 |
| customer_service | 4 | d18：store「每会话一条当前确认」语义（save 整行覆盖）与测试构造不兼容；cs_experts：290d2b7 refund_request 专用真实链路；p5 1：CI 时序竞争（本地 1495 passed 未复现） | #158 |
| prompts-context | 2 | auto_compact：fixture 12 轮太短，重建后固定 policy 开销反超压缩收益；collapse：本地 xdist 467 passed 未复现 | #158 |
| evaluation | 2 | 37ae046 temperature 0.1→0；8a97a63 数据集版本 candidate.4（loader 对齐 manifest 权威） | #158 |
| repo-tests | 1 | a9184e4 同 request_id 重启流 409（stream_registry 进程级单例跨用例存活） | #158 |
| （workflow） | — | Regression summary 恒红：toJSON(needs.*) 单依赖序列化为数组（全部历史 run 实证） | #158 |

## 剩余计划（唯一残余，下一会话）

**services-travelv2 3 例（run 41205470228 清单，模型注册表共享状态家族）**：

- `memory/test_memory_supersede_atomicity.py::test_b69_supersede_rollback_keeps_old_active`
- `memory/test_stop_e_summary_contract.py::test_end_turn_schedules_summarize_without_awaiting`
- `services/test_model_config_persistence.py::test_create_provider_without_probe_saves_directly`

根因方向：`configure_specialized` 抛 `ModelConfigConflict: 已有向量时不能通过旧专项接口切换嵌入配置`——mock 会话夹具的 canned 数据与角色绑定预检/确认/重建新流程错位（模型治理域，cs-workstation-v2 P2 #157 面）。注意 `specialized_model_config` 2 例在本轮已过（同家族、DB 状态敏感），定性时以 CI 忠实环境为准。方法照旧：单跑 → xdist → CI 模拟 env 三步。

---

# 交接（2026-10-31 深夜，供下一会话按此续工）

## 一、总计划

任务目标只剩最后一跳：**清零 services-travelv2 的 3 例残余 → 全量 run 13 suite + Regression summary 全绿 → 任务 COMPLETED**。

工作循环（本任务四批验证有效的四步法，顺序不可逆）：

1. **定性**：单跑失败文件 → xdist 对照 → CI 模拟 env 复现；`git log -S` 找引入提交，判「测试资产漂移 vs 实现回退」。本任务 108+ 例失败中仅 1 处需修实现（reporter sql_empty），其余全是漂移——先怀疑测试侧。
2. **修复**：对齐新契约或修 fixture 数据；断言不放宽、mock 不扩大、不 skip/xfail 掩盖；涉及并行会话领域（模型治理）先核对对方演进再动手。
3. **验证**：本地对应目录 xdist 跑绿 → PR 合 main → dispatch `suites=services-travelv2` 实证 → 台账 +1 行。
4. **收官**：dispatch 无参数全量 → 13 suite + Regression summary 全绿 → TASK.md 状态改 COMPLETED，PROGRESS.md 记最终曲线（v1 108 → 0）。

## 二、进度锚点

- main = f32b37b（#161 台账收官）。本任务已合 PR：#153（orchestration 7 + inventory 10）、#154（misc 12 + lock 补 kafka-python）、#158（22 例 + summary 表格）、#159/#161（台账）、#160（批四 13 例 + 125 登记 + wallet REVOKE）。
- 收敛曲线：**v1 108（run 40948443665）→ v2 61（41120872440）→ v3 20（41151145995）→ v4 3（41205470228，12/13 suite 全绿 + summary 绿）**。
- v4 唯一失败 suite：services-travelv2，失败清单 `/d/tmp/ci-debt/v4travelv2/`。
- 历史坑位 1–16 在 [PROGRESS.md](PROGRESS.md)（重点：6 资产漂移是主旋律、7 CI env 差异先于 xdist 猜测、13 分支 parent 核对）。

## 三、工作树与环境

- 续工工作树：`D:/Program Files/workplace/agent-wt-ci-debt2`（任务专用，保留）。
- 开工序列：`cd "/d/Program Files/workplace/agent-wt-ci-debt2" && git fetch && git log --oneline -1 origin/main && git checkout -b fix/ci-debt-model-registry origin/main`——**分支建好后立刻核对 parent commit 是预期值**（坑位 13：fetch 静默失败 → checkout 静默回退工作树，本会话实证一次）。
- `.env` 已在（gitignore，测试连库必需）；本地资产 `/d/tmp/ci-debt/`（可能被清理，权威清单以 GitHub artifacts 为准）；网络走代理 `HTTPS_PROXY=http://127.0.0.1:7897`，gh/git 抖动重试即可。
- 本残余预计纯测试改动，无需 rebuild；若定性需修产品代码，主仓库 `D:/Program Files/workplace/agent`（main）`git pull` 后 `echo y | ./devctl.bat rebuild` 并确认容器 healthy。

## 四、测试

- 单跑定性（**PGHOST 必须 127.0.0.1**，坑位 8：localhost 每连 ~5s）：
  `PGHOST=127.0.0.1 D:/Python/python.exe -m pytest backend/tests/memory/test_memory_supersede_atomicity.py backend/tests/services/test_model_config_persistence.py --timeout=150 -q --no-cov --tb=short`
- 套件回归（预期残余恰好 3 例、其余全绿；多出失败先按坑位 13 查工作树是否回退）：
  `PGHOST=127.0.0.1 D:/Python/python.exe -m pytest backend/tests/services backend/tests/memory -n 4 --dist loadfile --timeout=150 -q --no-cov`
- CI 实证：`HTTPS_PROXY=http://127.0.0.1:7897 gh workflow run full_regression.yml -f suites=services-travelv2`；失败清单 `gh run download <run_id> -n failures-services-travelv2`（gh 需在 git 仓库内执行，坑位 12）。
- 关键代码/测试文件：`backend/services/model_config.py`（`configure_specialized` ~1051 行的 ModelConfigConflict 抛出点）、`backend/tests/services/test_specialized_model_config.py`（`_session_stream` 假 session 模式——三例残余同款夹具）、`backend/tests/memory/test_memory_supersede_atomicity.py`、`backend/tests/memory/test_stop_e_summary_contract.py`、`backend/tests/services/test_model_config_persistence.py`。领域参考 [model-provider-runtime.md](../../governance/model-provider-runtime.md)。

## 五、验收

- [ ] dispatch `suites=services-travelv2` → pytest success（failures 清单为空）。
- [ ] dispatch 无参数全量 → **13 suite 全绿 + Regression summary 绿**（v1 108 → 0 收官）。
- [ ] TASK.md 状态改 COMPLETED，PROGRESS.md 回填最终曲线与收官 PR 号。
- [ ] 全程不扩大 mock、不删断言、不 skip/xfail；若定性为产品 bug（预检流程误伤合法路径），先报告再修实现并带回归测试；涉及 #157/模型治理域的改动在 PR 描述中显式标注协同边界。

## 验收

- 每周 Full Regression step summary 失败数逐周下降至 0（**v1 108 → v2 61 → v3 20 → v4 3**）。
- ~~Regression summary 表格修复后，suite 全绿时 run 才能真正变绿~~（已达成：run 41205470228 全绿 suite 下 summary 绿）。
- 台账与 PR 描述如实记录；容器级部署验证 UNVERIFIED 必须显式标注。

## 纪律

- 所有改动一律走 PR 合 main；提交用 `git add -A -- <paths>` + `git commit -m "..." -- <paths>` 双重路径限定。
- 不扩大 mock、不删断言、不 skip/xfail 掩盖失败；未实际执行的验证不得报通过。
- 本会话四批全部为测试资产 + CI workflow 改动，未动产品代码，无需 rebuild。
- 数据文件（suite 声明 candidate.1 vs manifest candidate.4）的不一致属 DATA-01 数据链流程，未在本任务扩范围（测试注释已标注）。
- 并行会话协同边界：sql-infra 授权回收（#160）是对 #157 引入越权的守卫响应；125 分类补登记同理。模型注册表测试家族的修复涉及对方领域，动手前先核对 #157 之后的演进。

## 关联

- 前序：PR #126–#132、#136、#139–#145、#149、#150（任务文档）。
- 本会话已合 PR：#153（orchestration 7 + inventory 10）、#154（misc 12 + lock 补 kafka-python）、#158（第三批 22 例 + summary）、#159（台账）、#160（批四 13 例 + 125 登记 + wallet REVOKE）。
- 本任务文档：[PROGRESS.md](PROGRESS.md)（含 v1→v2→v3 曲线、实证数据、坑位记录）。

---

# 交接（2026-11-01，第三批会话批五~批十二，供下一会话续工）

## 一、总计划（剩余一跳半）

1. **api 确认并入全量，勿单跑抽签**：#181（cs_agent_status_transfer 的 .env 依赖修复，本地 10 passed）已合并；但 api **单 suite dispatch 6/6 次全灭**（run 41252686538/41256263644 等，~40-56min 被 job 上限杀，坑位 21 的 runner 挂死形态在单跑下必现）——并发全量形态下 api 历次均正常出结论（v5 4 分钟、v14 正常）。处置：backend-root 清完后直接无参数全量 dispatch，api 结论从全量取。
2. **backend-root 首曝 54 例清偿**（v14 run 41249581757 首次真实跑完暴露，清单 `gh run download 41249581757 -n failures-backend-root`；本地副本 `/d/tmp/ci-debt/v14full/`）。
3. 全量 dispatch → 13 suite + summary 全绿 → COMPLETED。

## 二、批五~批十二已合 PR 与实证（勿重做）

| 批 | PR | 内容 | 实证 |
|---|---|---|---|
| 五 | #168 | api 13 例：11 例 internal-token fail-closed 注入（44d2e53 收口；in(401,403) 6 例注入 messaging.AI_INTERNAL_TOKEN→401；==403 5 例 setattr deps.ALLOW_UNAUTHENTICATED→enforce 403）+ 2 例 JWT_SECRET setenv | 本地 11+90 passed |
| 六 | #171 | maintenance run_sync 二元组契约（36f40b9）+ p5/collapse 诊断断言 | v8：maintenance 绿 |
| 七 | #172 | workflow 补 REDIS_ENABLED=true+REDIS_URL（p5 根因：缺省 false → get_redis() 静默 None → presence fail-closed） | v9：cs 全绿（p5 绿） |
| 八 | #174 | collapse 锁定 LLM_CONTEXT_LENGTH=8192 口径（CI 缺省 4096 → L2 先行裁剪吃掉折叠候选） | v10：prompts 绿 |
| 九 | #176 | workflow -rf→-ra + artifact 补 pytest_out.txt（error 类失败诊断资产） | v11：定位 2 errors |
| 十 | #179 | sla_graded `_pg()` open('.env') → MEMORY_DB_CONFIG | error 转可跑 |
| 十一 | #180 | sla_graded _seed 补 updated_at 置 stale（close_stale 按 updated_at；本地靠主栈 reaper 兜底掩盖） | v13：cs success |
| 十二 | #181 | cs_agent_status_transfer 同款 .env 依赖修复 | 本地 10 passed；api 定向待确认 |

## 三、backend-root 54 例定性方向（批十三工作清单）

v14 首曝（v5/v6 该 suite 均被 runner 杀掉从未跑完）。pytest_out.txt 全 traceback 在 artifact。按文件分组预判（以 pytest_out.txt 实证为准）：

- **.env 依赖类**（批十/十二同款）：grep `open(".env")` 全 tests/ 根目录清一遍，统一 MEMORY_DB_CONFIG。
- **REDIS_ENABLED=true 连带**：`test_admin_task_ops::test_redis_unreachable_returns_skeleton`（断言 4==5 队列数）——该用例语义是「Redis 不可达降级」，现在 Redis 可达；改法：用例内 mock/隔离 Redis（保留降级语义测试）或用独立开关。
- **anti_ban 配额状态泄漏**（4 例）：进程级共享配额被同 worker 前序用例耗尽（BudgetExhaustedError/PlatformStoppedError）——xdist 顺序敏感；修法参考坑位 11（fixture 加固/重置单例）。
- **真资产漂移**（逐例三步法）：`test_audit_fixes`（_process_pending 缺 state 参数——签名漂移）、`test_competitor`（10+ 例断言/输出格式漂移）、`test_prompt_contract_lock`（prompts.lock.json 与 DB 漂移——注意 DATA-01 边界）、`test_production_auth`（startup 校验规则漂移）、`test_llm_resilience`（ContextBudgetExceeded）、`test_hybrid_gate_score_scale`、`test_map_lookup_direct_contract`、`test_rag_p1_self_correction`、`test_rag_upload_sync_impl`（lambda 签名）、`test_consistency_report`、`test_p3_silent_failure_guards`（deps.py 裸 except——注意可能是真产品问题，按坑位 6 先定性）、`test_business_stores_pg`（pgtest 表 UndefinedTable——xdist 竞争）。
- **乱码**：断言消息中文在 CI 日志 GBK 显示乱码——是显示层噪音，修复时以 pytest_out.txt（UTF-8）为准。

## 四、工作循环与纪律（沿用）

四步法（定性→修复→验证→台账）不变；**优先用诊断断言**（坑位 18）：对 CI-only 疑难先加断言消息携带分流数据，一次 dispatch 拿决定性证据再修，避免盲猜循环。全程不扩大 mock、不删断言、不 skip/xfail；提交 `git add -A -- <paths>` + `git commit -- <paths>` 双重限定；所有改动走 PR。

## 五、环境与坑位

- worktree：`D:/Program Files/workplace/agent-wt-ci-debt2`（分支 docs/ci-debt-final 已全合并，续工 `git fetch && git checkout -b fix/ci-debt-backend-root origin/main`）。
- 本地资产 `/d/tmp/ci-debt/`（v5~v15 各 run 失败清单与 job 日志）；权威以 GitHub artifacts 为准。
- 网络：代理 7897 可能不可用，直连 api.github.com 通常可达；git push 失败时走 `git push ssh://git@ssh.github.com:443/Jayus-07/agent.git <branch>`。
- 坑位 17–21 见 [PROGRESS.md](PROGRESS.md)（17：gate skip 的秒级 success 勿当全量证据；18：诊断断言；19：from-import 的 patch 目标是子模块属性；20：本地环境红利清单；21：三 suite 挂死形态与 pytest_out.txt 诊断）。

## 六、验收（更新）

- [ ] api 定向绿（#181 后 dispatch）
- [ ] backend-root 清零（批十三）
- [ ] 全量 dispatch → 13 suite + summary 全绿 → TASK.md COMPLETED
- [ ] 曲线补记：v5 15 → v6/v10 挂死干扰 → v14 54（backend-root 首曝）→ 0
