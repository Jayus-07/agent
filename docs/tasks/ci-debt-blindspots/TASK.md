# CI 全量覆盖盲区收口 + 守卫自动化

> 任务 ID：ci-debt-blindspots
> 创建：2026-11-02（守护轮会话立项；前序 ci-regression-debt 已 COMPLETED）
> 状态：RUNNING（计划就绪，待执行；预计 1 个会话约半天）

## 目标

1. 收口 Full Regression 的覆盖盲区：backend/tests 下 7 个目录、19 个 test 文件、**209 个用例**不在 13-suite matrix 内，从未在全量口径（xdist + CI env + 隔离库）下跑过——收进 matrix 并清偿首曝。
2. 两个防漏守卫自动化：matrix 目录覆盖守卫（workflow 内置）+ flushdb 分片守卫（测试资产），让「漏登记」「裸 db flush」两类回归由 CI 识别。
3. 文档同步三小项（见 §测试/验收）。

## 背景（自包含；细节以本文件与 PROGRESS.md 为权威）

- 前序收官：v28 全绿（run 41358679667）→ 守护轮 v29（run 41363455037，main@257af71）12/13 绿，backend-root `test_heartbeat_renews_admission_token` 间歇红 = 坑位 31（#237 分片收口漏 test_distributed_gate.py / test_tool_stats_aggregation.py，裸 db15 跨 worker flush）→ 修复后 **v30 全绿（run 41365926765，14/14 job）**。PR #251 已合 main（merge 832a572；docs a20367f + fix 9bc3cc2 + PROGRESS ae292c6）。T1（#232）全量验证过关（v29 travel 绿）。
- **盲区发现（本任务立项依据，2026-11-02 实证）**：`full_regression.yml` 13-suite matrix 的 paths 为显式清单，`backend-root` 只展开根散文件不递归；对差（`ls backend/tests/*/` vs matrix paths）发现 7 个目录不在任何 suite：
  `agents(2 文件) / eval(2) / router(1) / selection_decision(2) / selection_funnel(9) / tasks(1) / tool_governance(2)`（`fixtures` 无 test 文件豁免；`__pycache__` 忽略）。
  collect 实证：`pytest <7 目录> --collect-only` = **209 tests collected**（15.22s，无 import 错误）。这些用例 PR 定向清单与每周全量都不跑 = 持续盲区。
- 复核命令（动手前先重跑确认无新增目录）：
  `ls -d backend/tests/*/ | sed 's|backend/tests/||;s|/||' | sort > /tmp/actual && grep -o 'backend/tests/[a-z_0-9]*' .github/workflows/full_regression.yml | sed 's|backend/tests/||' | sort -u > /tmp/matrix && comm -23 /tmp/actual /tmp/matrix`

## 一、总计划（按序，顺序不可逆）

1. **P0 · scheduled 首跑核对**（只读，几分钟）：Full Regression 首个 cron 触发点 = 2026-11-02 03:00 UTC（workflow 10-30 建立，此前无 schedule 历史；GitHub schedule 高峰期可延迟 15–45min）。`HTTPS_PROXY= gh run list --workflow=full_regression.yml --event=schedule --limit 1`——结论应与 v30 一致（13/13）。若红：先看是否 dispatch/schedule 口径差（无 inputs vs 有 inputs），再按四步法定性。
2. **P1 · matrix 收口 + 209 例首曝清偿（主体）**：
   a. `full_regression.yml` misc suite 的 paths 追加 7 目录（现有 13 目录后追加 `backend/tests/agents backend/tests/eval backend/tests/router backend/tests/selection_decision backend/tests/selection_funnel backend/tests/tasks backend/tests/tool_governance`）；209 例体量约 2–4 分钟，无需新 suite。注释里记录盲区发现与收口（209 例 / 2026-11-02 对差实证）。
   b. 本地先跑一轮这 7 目录（xdist 口径）拿首曝预览，能修的先修。
   c. PR 合 main 后 dispatch `suites=misc` 定向一轮拿 CI 首曝清单，按四步法清偿（预期风险见 §测试）。
   d. 清偿完 dispatch 无参数全量 → 15 job 全绿收官。
3. **P1.5 · 两个防漏守卫**：
   a. **matrix 覆盖守卫**：full_regression.yml 加独立小 job（checkout + 对差脚本），断言 `backend/tests` 目录全集（排除 `__pycache__`/`fixtures`）⊆ matrix paths，非空即红。放 `needs: pytest-suite` 之前或并列均可，must fail 当新顶层目录漏登记。
   b. **flushdb 分片守卫**：新增测试文件（建议 `backend/tests/infra/test_redis_test_hygiene.py`，归 sql-infra suite）静态扫描 `backend/tests/**/*.py` 源码：断言出现 `Redis.from_url` 的文件集合 ⊆ 白名单（fixtures/redis_test.py + 五个已分片文件：test_task_admission.py / test_task_admission_runtime.py / test_stop_g4_matrix.py / test_distributed_gate.py / test_tool_stats_aggregation.py），出现 `flushdb` 的文件同理；集合式断言防新增漏网（坑位 31 的自动化）。
4. **P2 · 文档三小项**（可与 P1 同 PR）：
   a. `docs/operations/ci-evaluation-environment.md` §8 全量回归行：覆盖承诺补「13-suite matrix = backend/tests 目录全集（含守卫）」+ 诊断资产指引（PYTEST_XDIAG 三件套 → testing-guide 新节）+ 用例数口径刷新（约 12500 → 含 209 例收口后的数字）+ 快照日期刷新（现停 2026-10-30 / 基线 2517e98）。
   b. `docs/operations/commands.md`：新增「CI 守护常用命令」小节（dispatch 全量/单 suite、轮询、artifact 下载、xdiag 判读入口、scheduled 核对）。
   c. `docs/development/testing-guide.md`：补连带规则「新增 backend/tests 顶层目录必须同步 full_regression matrix」（放「常用运行形式」附近 flushdb 纪律同节）。
5. **P3 · worktree 清理**（收官后）：agent-wt-ci-debt2 与本任务工作树先查残留再清（合并后才允许）。

## 二、工作树与环境

- 新建任务工作树（勿复用 agent-wt-ci-debt2，其历史已收官）：
  `cd "/d/Program Files/workplace/agent-wt-ci-debt2" && git fetch && git checkout -b fix/ci-debt-blindspots origin/main`，或从主仓库新建 `git worktree add "/d/Program Files/workplace/agent-wt-ci-blindspots" -b fix/ci-debt-blindspots origin/main`。**分支建好后立刻核对 parent commit ≥ 832a572**（坑位 13：fetch 静默失败会静默回退）。
- worktree 无 .env（gitignore）：需要时从主仓库 `D:/Program Files/workplace/agent/.env` 拷贝。
- 网络：gh 直连（命令前缀 `HTTPS_PROXY=`）；git push 走 `git push ssh://git@ssh.github.com:443/Jayus-07/agent.git <branch>`；抖动重试。
- 本地资产 `/d/tmp/ci-debt/`（v19–v30 各轮清单/xdiag；权威以 GitHub artifacts 为准）。
- 本任务全部为测试/workflow/文档改动，预期不动产品代码，无需主栈 rebuild；若定性出真产品缺陷（参照批十五先例），修后主仓库 pull + rebuild + 确认 healthy。

## 三、进度锚点

- 起点 main：≥ 832a572（#251）。已合 PR：#251（守护轮 docs+fix+PROGRESS）。
- 盲区证据：209 例 collect（2026-11-02）；对差输出见背景；misc suite 现有 paths 清单见 workflow matrix 段。
- 坑位 1–31 见 [ci-regression-debt/PROGRESS.md](../ci-regression-debt/PROGRESS.md)；本任务新坑位续接 32 起记入本目录 PROGRESS.md。

## 四、测试

- **本地四步法**（顺序不可逆）：单跑（`PGHOST=127.0.0.1` + `--timeout=150`）→ xdist（`-n 4 --dist loadfile`）→ CI 模拟 env（`env -u` 清红利键：REDIS_ENABLED/LLM_CONTEXT_LENGTH/JWT_SECRET/AI_INTERNAL_TOKEN/SECRETS_ENCRYPTION_KEY/NLI_USE_LLM 等，**int 键置空会炸**）→ CI 定向实证。
- 7 目录本地基线：`PGHOST=127.0.0.1 D:/Python/python.exe -m pytest backend/tests/agents backend/tests/eval backend/tests/router backend/tests/selection_decision backend/tests/selection_funnel backend/tests/tasks backend/tests/tool_governance -q --no-cov --timeout=150 -n 4 --dist loadfile`
- CI 定向：`HTTPS_PROXY= gh workflow run full_regression.yml --ref <分支> -f suites=misc`；失败清单 `HTTPS_PROXY= gh run download <run_id> -n failures-misc`（gh 需在 git 仓库内执行）。
- 守卫自验证：matrix 守卫可临时在对差脚本里加假目录验证会红后还原；分片守卫可用「在临时文件里写一个裸 `Redis.from_url`」验证会红后删除。
- **首曝预期风险**（按坑位 26–31 预判，以实证为准）：专用 PGDATABASE 键族缺省兼容库（坑位 28 家族第四次复发，SELECTION_* 已显式化，注意 selection_funnel/selection_decision 用例的库键）；.env 红利清单（坑位 20/25）；tool_governance 的 govtest 残留与建表依赖（PROGRESS 移交产品域提过 finally 缺口）；drop_pgtest_tables 库清单覆盖；xdist worker 分片。

## 五、验收

- [ ] P0：scheduled 首跑结论与 v30 一致（13/13），或按四步法清偿后一致。
- [ ] P1：misc suite 收口 7 目录；dispatch `suites=misc` 首曝清单清零（不 skip/xfail、不放宽断言、不扩大 mock）。
- [ ] P1：dispatch 无参数全量 → **15 job（13 suite + summary + matrix-coverage）全绿**，用例口径含 209 例收口。
- [ ] P1.5：两个守卫在位且验证过「会红」（临时注入假违例验证后还原）。
- [ ] P2：三处文档更新合入，与 workflow/测试实态一致（文档标准：文档与代码冲突时以实态为准修正）。
- [ ] 收官：TASK.md 状态改 COMPLETED，本目录 PROGRESS.md 回填曲线（首曝数 → 0）、run 号与坑位。

## 六、纪律与边界

- 所有改动走 PR 合 main；提交 `git add -A -- <paths>` + `git commit -m "..." -- <paths>` 双重路径限定；合并前 `gh pr view --json headRefOid` 核对 head。
- 诊断断言先行（坑位 18）：疑难先加断言/打点一轮 dispatch 拿证据，不盲修。
- 边界（不动）：移交产品域两项（progit PDF 4 解析异常、govtest finally）；其他会话线（travel-proto-production、trace-* 任务）；rag_smoke 定向清单不扩（209 例进全量 matrix 即可，定向清单是 PR 门禁快口径）。
- 未实际执行的验证不得报告为通过；暂停/取消按 AGENTS.md 长任务续工契约标记 PAUSED/BLOCKED/CANCELLED。

## 七、持续实施计划（2026-11-02 核实后）

持续目标已启用；保持当前工作树与分支，不重复开工或审计全仓。

1. **证据复核与分类**：保存209例基线（189通过/20失败），逐组对照当前源码、契约与既有授权测试；分别确认夹具漂移、预期过期或产品缺陷。停止条件：20例都有根因与对应处理，不靠跳过或削弱断言。
2. **最小修复**：先单跑选品API失败代表，复用现有可信用户夹具；再定位Router决策与治理missing_specs，不把现有行为直接当正确预期。若需产品改动，补相应测试并遵守安全审查、本机重建健康验收。
3. **本地验收**：失败代表单跑→七目录209例四worker/loadfile→清除.env红利的CI环境模拟；同时检查权限边界回归。定向T0语法/diff和必要T1/T2，禁止默认全量本地回归。
4. **守卫自查**：matrix覆盖精确读取实际suite paths；Redis静态扫描覆盖既定调用点；两者分别注入假违例、记录失败退出码、还原并通过，避免并发注入污染正常验收。
5. **文档与本地交付**：同步三处现行文档，统一新增守卫后的15-job口径，回填实际数量与证据；最终Diff自查，给出明确提交范围和验证结果，依AGENTS.md第4条取得提交确认。
6. **PR与CI**：确认后限定路径提交/push/建PR；遵守门禁与最多一次主动完整云端Review规则，不发送@codex review。核对PR head后按授权合并；main上dispatch misc，逐轮诊断清偿，保留失败清单和run号。
7. **全量与收官**：无参数dispatch，13 suite+guard+summary共15 job全绿；核对scheduled首次运行（若尚未触发则保持待验收）；最终文档状态与证据同步，所需后续提交也走PR；全部验收后标COMPLETED。最后检查未提交、未合并及忽略资产，安全清理已合并工作树。

**执行纪律**：本地通过不等于CI验收；首曝规模超出会话容量时按用户契约记录清单，未完成不得标完成。任务当前RUNNING；只有明确暂停指令才暂停持续目标。提交/合并确认是外部依赖，先完成所有可独立推进的本地工作。
