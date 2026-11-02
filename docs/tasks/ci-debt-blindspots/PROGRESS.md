# PROGRESS — ci-debt-blindspots

> 进度台账：证据、阻塞、下一步。坑位续接 ci-regression-debt 的 1–31，从 32 起记入本文件。

## 立项记录（2026-11-02，守护轮会话分析产出）

### 已完成事实（立项依据，勿重做）

| 事项 | 证据 |
| --- | --- |
| T1（#232）全量验证过关 | v29 run 41363455037（main@257af71）travel suite 全绿 |
| v29 唯一红已根因清偿（坑位 31：#237 分片收口漏 test_distributed_gate.py / test_tool_stats_aggregation.py，裸 db15 跨 worker flush） | HB-DEBUG（心跳 8 轮全成功 + all_keys=[]）+ xdiag 时间线（gw1 dgate vs gw0 admission）；存档 /d/tmp/ci-debt/v29/ |
| 守护轮修复 PR #251 合 main | merge 832a572；提交 a20367f（testing-guide 两小节）+ 9bc3cc2（两文件分片）+ ae292c6（PROGRESS 回填） |
| 收口轮 v30 全绿 | run 41365926765（9bc3cc2）14/14 job success |
| testing-guide.md 已含 PYTEST_XDIAG 三件套 + flushdb 分片纪律 | #251（a20367f） |
| **盲区发现：7 目录 19 文件 209 用例不在 full_regression matrix** | `ls backend/tests/*/` vs matrix paths 对差；`pytest <7 目录> --collect-only` = 209 tests（15.22s 无 import 错误）；目录：agents/eval/router/selection_decision/selection_funnel/tasks/tool_governance |
| flushdb 调用点五个文件全部分片 | grep 实证（admission×2 / runtime / dgate / tool_stats） |

### 当前状态

- TASK.md 已立项（总计划 P0–P3、验收清单就绪），等待下一会话执行。
- scheduled 首跑（cron 2026-11-02 03:00 UTC）截至立项时未到点/未出现，P0 待核对。

### 下一步（下一会话第一动作）

1. 按 TASK.md §二建工作树与分支（parent ≥ 832a572）。
2. P0 scheduled 核对。
3. P1 matrix +7 目录 → 本地 xdist 基线 → PR → dispatch suites=misc 首曝清偿 → 全量收官。

## 坑位（32 起，续接 31）

（暂无；首曝清偿过程中按「一轮一证据」纪律补充。）

## 阻塞与移交

- 无阻塞。移交产品域两项维持边界（见 TASK.md §六）。

## 2026-11-02 本地施工与首曝

- 工作树：`D:/Program Files/workplace/agent-wt-ci-blindspots`；分支 `fix/ci-debt-blindspots`；HEAD `84601db`（origin/main），当前改动未提交。
- P0：`gh run list --workflow=full_regression.yml --event=schedule --limit 1 --json ...` 返回空数组；查询时北京时间约 09:00，尚未到 11:00 cron，待到点核对。
- P1：misc 已追加七目录；本地四 worker / loadfile / timeout=150 实跑 **189 passed, 20 failed / 118.48s**，日志 `d:/tmp/ci-blindspots-baseline.log`。这不是 CI 首曝结论。
- 失败分组：Router 6 例 execution_decision 为 plan 而预期 direct；selection_funnel 7 例 + selection_decision 5 例写接口 403；Tool Governance 2 例库存/数量断言。权限源码核对：写接口现使用 require_editor_user，旧夹具仅覆盖 resolve_operator_role；尚未修改夹具。Router 与治理两组根因未定，不允许放宽断言。
- P1.5：matrix 守卫正常覆盖 37 目录；注入 blindspot_probe 目录实证退出失败，已移除。Redis AST 调用扫描守卫注入 infra/test_hygiene_probe.py 的 flushdb 实证失败，已删除；还原后 pytest **1 passed / 20.39s**。首次并发 pytest 恰逢注入窗口而红，不能计为还原后的验证。
- T0：两新增 Python 文件 py_compile 已执行通过；git diff --check 无错误。三处文档已施工，但最终数量/CI 快照仍待收官。
- 验收口径修正：新增独立 matrix-coverage job 后应为 **15 job（13 suite + summary + guard）**，不再是 14。
- 未完成：20 例清偿、CI 模拟 env、scheduled 核对、提交/PR/misc CI/全量 CI、最终文档回填和清理。状态仍 RUNNING，不标 COMPLETED。
- 下一步：先单跑各失败代表并核对现行契约，修夹具而不削弱权限；治理 missing_specs 与 Router 决策根因需继续取证。提交必须先展示最终 Diff 和验证结果并取得明确确认（仓库 AGENTS.md §Git 并行开发规范第4条）。

## 2026-11-02 持续实施：首曝第一批

- 已修复 selection_decision/test_decision_api.py、selection_funnel/test_api_import.py、selection_funnel/test_market_data.py 的写接口授权夹具：现行写门禁直接调用 require_user_actor/resolve_operator_role，旧 FastAPI dependency override 不会覆盖普通函数内部调用。业务夹具显式提供 require_editor_user 的 editor 身份，不改产品门禁。
- 实跑上述三文件 + 既有 api/test_selection_routes_authz.py：**36 passed / 37.59s**，资产 `d:/tmp/ci-blindspots-api.log`。涵盖 viewer 写拒绝与无凭据拒绝。
- Router 诊断资产 `d:/tmp/ci-blindspots-router-diag.log`：旧层级元数据候选 permission_ready=false，selection_mode为空；旧 direct override 同样不具备安全候选。现行 ExecutionModeResolver 明确规定 direct 不是授权。更新旧测试为精确断言 plan/clarify，保留域/能力连续性与旧字段不变断言，新增未经授权候选检查；未修改运行代码。
- 治理诊断 `d:/tmp/ci-blindspots-gov-report.log`：capability_spec_count=20、tool_spec_count=60；missing_specs 恰为 booking_quote_tool/food_search_tool/hotel_search_tool，bypasses=0。manifest 中三个 travel.* capability 已声明，但工具实现名对应 ToolSpec 缺失；属于需核实并修复的治理声明缺口，不可删除库存断言。下一步对照 manifest tool_specs 与既有生成器，避免建立平行注册表；若改运行声明，需按项目规则验证重建与安全审查。
- 持续目标保持 active，TASK RUNNING；尚未提交或创建PR。

## 2026-11-02 治理缺口修复与复验

- Router 精确安全断言 + 既有LLM路由安全测试：**25 passed / 17.33s**（`d:/tmp/ci-blindspots-router-fixed.log`）。
- 治理：在现有 capabilities.yaml 的 tool_specs 补入 food_search_tool/hotel_search_tool/booking_quote_tool，参数严格对应已提交 tool_contracts.lock.json 的实现契约（酒店日期在实现为可选，未误用更严格的能力入参）；治理策略分别继承对应 travel.* 能力的 read/R0 与 create/R1，不执行预订，确认门未修改。能力数量守卫17→20、ToolSpec最低数56→63。
- 治理目录实跑 **12 passed / 25.01s**，资产 `d:/tmp/ci-blindspots-gov-fixed.log`。manifest 属运行声明，后续必须重建验收，并核对公共契约生成器是否需同步。
- 七目录209例四worker复验已启动，输出 `d:/tmp/ci-blindspots-baseline-fixed.log`；终态待确认，不报告通过。下一步：确认复验终态→CI环境模拟→双守卫最终自查→本机重建→最终Diff/提交确认→PR与CI。

## 2026-11-02 全量本地复验通过

- 七目录四worker/loadfile：**209 passed / 115.49s**，`d:/tmp/ci-blindspots-baseline-fixed.log`；本地首曝曲线20→0，CI首曝尚未执行。
- `PYTHONPATH=<worktree> python backend/scripts/gen_tool_contract_lock.py --check`：**IN_SYNC，BREAKING=0 DEGRADED=0 COMPATIBLE=0**；无需改派生lock。首次未设置PYTHONPATH的直接脚本调用import失败，已更正执行方式。
- matrix守卫收紧为实际内联suite条目，避免注释路径算覆盖；summary needs纳入matrix-coverage。
- CI环境模拟已启动：`PYTHON_DOTENV_DISABLED=1` 禁止所有dotenv加载，清除本地.env定义键，注入workflow业务env；仅PG连接凭据/端口沿用本机值，PGHOST=127.0.0.1。资产 `d:/tmp/ci-blindspots-ci-env.log`，不是Linux CI实证，结果待核对。
- 双守卫最终顺序注入/还原复验已启动，资产 `d:/tmp/ci-blindspots-guards.log`；不与同一守卫正常pytest并行。
- 本机重建尚未执行。当前已有 business-mock unhealthy（改动前docker ps实证），需区分既有健康问题与本次重建结果，禁止宣称全栈已健康。
- CI环境模拟终态：**209 passed / 49.37s，退出0**。附带residue-guard警告 embedding角色缺失，未将其视为本任务通过的产品检索验收。
- 双守卫最终注入/还原完成：matrix与Redis均违例退出1，还原通过。资产 `d:/tmp/ci-blindspots-guards.log`。
- 已从本任务工作树执行 `devctl.bat rebuild /y`，资产 `d:/tmp/ci-blindspots-rebuild.log`，终态与健康待验收。

## 2026-11-02 提交前自查

- 最终 Redis 守卫 pytest：**1 passed / 32.69s**（`d:/tmp/ci-blindspots-guard-final-pytest.log`）；全部7个新增/修改Python文件 py_compile 退出0。
- 本地Diff复核覆盖workflow/matrix/summary、三个授权夹具、Router fail-closed精确断言与三个治理声明。未修改运行授权路径、未新增skip/xfail、未扩大产品实现mock。
- 文档快照已刷新为2026-11-02，TASK全量验收修正为15job；真实CI数量仍待实跑，不伪造全量总数。
- 重建已完成镜像build，正在容器替换；最终退出码与健康仍待确认。P0 scheduled再次查询为空（尚未到cron）。
- 预计提交范围14文件（12 tracked修改+2新文件），保持未提交。下一步：重建健康/容器内声明验证→最终Diff摘要与明确提交确认→PR/CI；未获确认不提交。

## 2026-11-02 重建阶段实证与命令修正

- 后端镜像build、容器替换已执行，db-migrate退出0；容器内get_tool_spec三个声明实证为food/read/R0、hotel/read/R0、booking/create/R1。
- `devctl.bat rebuild /y` 默认还继续构建前端，发现日志进入frontend build即中止该进程（退出1），尚未看到frontend替换步骤；不能报告此命令成功。已修正为 `devctl.bat rebuild backend /y`，利用刚完成的BuildKit缓存，资产 `d:/tmp/ci-blindspots-rebuild-backend.log`。
- app健康仍starting，首次HTTP响应提前结束；未验收。下一步确认限定后端重建与健康终态，不能把声明读取成功等同完整部署通过。
- 最终Diff快照 `d:/tmp/ci-blindspots-review.diff` 已生成（含新文件）；实际提交前需刷新。

## 2026-11-02 本机运行验收与脚本限制

- 源码核实 dev-svc.bat do_rebuild 无条件调用rebuild_backend+rebuild_frontend，实际忽略TARGET；此前认为backend参数可限制范围的推断不成立。第二次在frontend build阶段中止（退出1），未进入frontend up；不再重复重建，不在本任务修脚本。
- 两次命令整体退出1不能记PASS；后端子阶段均已完成build/up。第二次后app /health **HTTP200/status=ok**，migrations=ok，schema_consistency=ok（72项无缺失），Redis healthy。
- 容器capabilities.yaml SHA256与本任务工作树均为 `bd0371c42aa3b17f02f2d24619d17e7fac87ed097542b6eb34fd6cfd6d8e466c`；三个新增ToolSpec已容器内读到。以实际内容校验弥补health build.commit=unknown，不宣称有Commit部署追踪。
- 最新观察多数后端healthy，maintenance/report worker暂starting，待终态核对。前端容器运行时长保持原值，未被本次替换。
- maintenance/report worker终态均healthy；本任务涉及后端代码服务均已健康。整体脚本受上述frontend范围限制中止，后端内容/HTTP/容器健康验收通过。
- 当前进入 WAITING_COMMIT_CONFIRMATION（TASK仍RUNNING）：等待用户明确同意提交本次14文件并推送建PR；尚无PR、无新Commit。真实misc/全量CI及scheduled未验收，不建议合并。

## 2026-11-02 已提交与真实CI

- 用户明确确认后限定14路径提交并push：`420f5ace967763c745d215b76412bd88cd3da40a`；PR [#261](https://github.com/Jayus-07/agent/pull/261) 已创建。此前WAITING_COMMIT_CONFIRMATION已解除。
- 分支misc定向run [41372835023](https://github.com/Jayus-07/agent/actions/runs/41372835023)：**925 tests collected，925 passed / 23.74s**，matrix-coverage通过；其他suite受参数过滤，不计全量验收。CI首曝清单为0，无需按预判盲修。
- PR secret-scan通过；RAG preflight仍运行；当前reviews与inline评论为空。未主动发送@codex review/security review。
- 已提前启动分支无参数全量run [41375707630](https://github.com/Jayus-07/agent/actions/runs/41375707630)，HEAD=420f5ac；matrix守卫success，13suite均实际in_progress。此轮可提前暴露跨suite回归，不能替代合main后最终验收。
- 未合并main；下一步等待preflight与分支全量，处理失败/审查证据→请求合并确认→main的misc/全量与scheduled验收→收官文档与残留检查。
- PR门禁已完成：secret-scan与RAG preflight成功，付费评测条件skip；PR mergeStateStatus=CLEAN，reviews仍为空。只读核对分支保护required_approving_review_count=0、无required status contexts；未修改任何设置。
- 分支全量13suite当前处于Install locked dependencies，run 41375707630活跃；跟踪会话已启动，输出 `d:/tmp/ci-blindspots-full-watch.log`。等待同一run，不重复dispatch。

## 2026-11-02 分支全量终态与合并待确认

- 分支无参数全量run **41375707630 / HEAD420f5ac：15/15 job success**。13个suite实际运行、matrix-coverage与summary通过，资产 `d:/tmp/ci-blindspots-full-41375707630/`。
- pytest实际口径：**12664 passed，142 skipped，9 xfailed**（既有条件跳过/预期失败，非本任务新增；不能写成全部用例执行通过）。misc925 passed，新增209例已纳入。
- PR门禁通过、head=420f5ace967763c745d215b76412bd88cd3da40a，reviews空；没有云端人工/模型Review结论，未将CI当作Review。分支保护不要求approving review。运行授权未改，治理声明本地Diff与定向验证证据见前文。
- 进入WAITING_MERGE_CONFIRMATION：建议合并PR261后main上misc+无参数全量验收；scheduled未到/未验证。未经确认不合main。PROGRESS本轮证据尚未提交，将在收官文档批次一起处理。

## 2026-11-02 恢复核对：scheduled补齐

- 当前PR261仍open、merged=false、head420f5ac；REST中merge_commit_sha是测试合并候选，不能当作已合并证据。
- scheduled run [41426703350](https://github.com/Jayus-07/agent/actions/runs/41426703350)，createdAt=2026-11-02T10:10:58Z，main@144aea7729b4e573b87e5ba34421c42e329c98d2：**14/14job success**，13suite+summary。此为旧matrix定时运行口径，P0已核对；不能替代本PR合入后的15job验收。
- 仍等待用户明确合并确认；目标恢复不等于同意合并。收官文档3文件目前未提交，保留不覆盖。

## 2026-11-02 用户确认合并与main最终验收

- 用户明确「确认合并#261」后核对head420f5ace967763c745d215b76412bd88cd3da40a，以match-head-commit正常merge，未绕过门禁。
- PR261已MERGED：merge **5723d354b3529122a706ffa2db9783d9e0270f3c**，mergedAt=2026-11-02T13:38:08Z。
- main misc定向run [41454697496](https://github.com/Jayus-07/agent/actions/runs/41454697496) 与main无参数全量run [41454704459](https://github.com/Jayus-07/agent/actions/runs/41454704459) 已触发。最终结果待验收；不能沿用分支全绿宣称main通过。
- 后续：两轮终态/用例数量→文档三文件收官→按明确确认规则提交文档PR→合入后检查并清理任务工作树及前序ci-debt2残留（不强制清理）。

## 2026-11-02 收官前工作树核对

- 本任务工作树已安全ff到origin/main@5723d35，3处收官文档改动完整保留，无冲突。快进带入上游已提交的旅游/客服修改，不计入本任务Diff，也不重复改动。
- P1.5双守卫、P2三处文档施工已随261合main，对应验收项已勾选；最终统计快照文档更新仍待收官PR。
- 前序ci-debt2 HEAD ae292c6为origin/main祖先，tracked/untracked干净，但两工作树均有.env和忽略资产；清理须先保留必要资产，不强制删除。不在CI尚未终态时清理本任务工作树。
- main misc run41454697496已success；全量41454704459已有11suite+guard成功，仅backend-root/rag仍执行，无已知失败。misc资产 `d:/tmp/ci-blindspots-main-misc-41454697496/`。
- 两工作树非缓存忽略资产已压缩并逐文件SHA256校验：`d:/tmp/ci-blindspots-preserved/`（含.env，勿上传）；前序5项/本任务4项。尚未删除工作树。

## 暂停记录（用户明确暂停，2026-11-02）

- 状态：PAUSED；任务ID ci-debt-blindspots。
- 工作树 `D:/Program Files/workplace/agent-wt-ci-blindspots`；分支 `fix/ci-debt-blindspots`；HEAD `5723d354b3529122a706ffa2db9783d9e0270f3c`（PR261已合main）。
- 未提交3文件：docs/operations/ci-evaluation-environment.md、docs/tasks/ci-debt-blindspots/TASK.md、PROGRESS.md。未stash/reset/清理。
- 验证：209例本地/CI环境模拟通过；分支全量41375707630为15/15success；scheduled41426703350为14/14success；main misc41454697496为925passed。
- main全量41454704459仍由GitHub运行：暂停前最后核对11suite+matrix守卫success，backend-root/rag in_progress；未取得最终结论，未取消run。
- 资产备份 d:/tmp/ci-blindspots-preserved/（含.env，不上传）；CI资产与日志见前文。
- 恢复：先读最新指令并核对工作树/HEAD/3文件/上游；查询同一run41454704459终态，下载资产核对数量与失败，不重复dispatch；通过后准备收官文档Diff并请求提交确认，再走文档PR/合并确认与安全清理。仍有任何未验收项不得标COMPLETED。

## 交接核对补充（2026-11-02，仅文档整理，目标仍PAUSED）

- 暂停后只读查明main全量41454704459已failure：14/15job成功，仅RAG失败。唯一用例test_metered_cloud_protocol_uses_same_rebuild_and_price_ledger被model_price append-only触发器拒绝；RAG为1failed/1709passed/2skipped。未修复或重跑，根因未确认。
- 已下载失败资产至d:/tmp/ci-blindspots-main-full-41454704459/failures-rag/，完整下一会话实施计划见HANDOFF.md；先清偿该失败，再最终全量与文档收官/安全清理。
- TASK头状态由过期RUNNING改为PAUSED；交接新增HANDOFF.md。当前未提交4文件，未提交/合并任何新内容，未恢复持续目标。

## 2026-11-03 RAG唯一失败根因定位与修复（待提交确认）

- 根因（测试隔离缺陷，非产品缺陷、非间歇红）：test_migration_residue_guard.py 的 finally 对称清理按 `price_table_version LIKE 'migration-test-%'` 前缀宽匹配软关价格行，无 stale 阈值、未限定本测试创建的行。main run 41454704459 中该测试在 gw2，与 gw3 上的 test_embedding_migration_worker.py 计量测试并行共库：gw2 把 gw3 刚插入的 fresh 价格行软关 → 计量查询（pricing.py:287 按 effective_to 过滤）落空，llm_usage.price_version='' → 272 行断言失败；gw3 finally 再按精确版本关行时 OLD.effective_to 已非空，被 append-only 触发器拒绝（053 触发器按 PL/pgSQL 行号规则恰为函数体 line 23，与 CI 报错一致）→ 掩盖原始断言错误。
- 排除项：cleanup_residue 与 worker_env 的 model_price 软关均带 60 分钟 stale 阈值，不会动 fresh 行；全仓其余 UPDATE model_price 仅治理服务三处按 provider/model 精确关闭，测试路径不触达；产品代码与触发器行为正常。
- 修复（backend/tests/rag/test_migration_residue_guard.py，+48/−12）：_make_residue 返回版本号；finally 改为按本测试登记的精确版本 `= ANY(%s)` 收口；新增并行竞态模拟行（独立 model_name='race-guard-sim'）与回归断言"模拟行必须仍开放"。
- 验证（本机隔离库 PGHOST=127.0.0.1:5433，pytest 定向）：修复版 2 文件 3 用例 3passed（92.01s）→ 注入旧前缀宽匹配后该用例失败且断言精准命中（"并行模拟的 fresh 价格行被本测试清理误关"）→ 还原后 3passed（34.13s）。按 testing-guide 最小充分原则未跑全量回归。
- 待确认：限定路径提交（仅 backend/tests/rag/test_migration_residue_guard.py）→ 修复 PR → 合并确认后 RAG 定向 CI 与 main 无参数全量 15/15 → 文档收官与清理。本修复为 test-only 改动，无需重建容器服务。

## 2026-11-03 travel bulkhead 竞态（第二起 suite 失败）根因与修复

- main 无参数全量 run [41493314368](https://github.com/Jayus-07/agent/actions/runs/41493314368)（HEAD 6b6b4ab，#264 合并后收官验证）唯一失败：travel `test_provider_bulkhead_rejects_fifth_call_without_queuing`（`all(started)` 断言 False）。同 HEAD run 41491867512 travel 全绿——同 SHA 两跑不同结果，属时序竞态。
- 根因（测试隔离缺口，产品行为正常）：`_provider_pool()` 是模块级 4-worker 共享单例线程池，夹具只重置了 `_provider_slots` 信号量。同 worker 前序用例运行中的 loader（`future.cancel` 停不掉）占住池 worker → 本用例 submit 排队 → 排队中的 future 在 2s 预算到期被 cancel，loader 永不启动。日志铁证：测试捕获 4 条预算告警但 4 个事件未全部置位，说明至少 1 次到期发生在排队未启动阶段。
- 修复（backend/tests/travel/test_provider_execution_bounds.py，+6 行）：夹具补 `_pool=None` 惰性重建（双重检查锁保证并发首调用安全）。
- 验证：A/B 脚本（占住 2 个池 worker 模拟前序残留）旧行为 `all_started=False, errors=[0,1,2,3]` 复现失败模式 → 新夹具 `all_started=True`；本文件本地 pytest 3 passed。PR #265 门禁全绿后合并，merge commit **c99270830718517454af40da17e31494c20813e5**。

## 2026-11-03 收官：main 无参数全量 15/15 全绿

- 最终验收 run [41497402474](https://github.com/Jayus-07/agent/actions/runs/41497402474)（HEAD c9927083，workflow_dispatch 无参数）：**15/15 job success**，13 suite 真实执行。
- 用例口径（各 suite pytest_out.txt 汇总）：**12688 passed / 142 skipped / 9 xfailed**。分 suite：api 912、backend-root 2278+7skip、customer_service 1535+3skip、evaluation 529+42skip、misc 925、orchestration 1053、prompts-context 463+4skip、rag 1710+2skip、repo-tests 109、services-travelv2 373+76skip、sql-infra 779、tools-security-obs 554、travel 1468+8skip+9xfailed。RAG 由此前 1 failed/1709 passed 转为 1710 passed。
- 曲线：本地首曝 20→0；CI misc 0；main 全量 RAG 1→0、travel 1→0（两起均为 suite 内测试隔离竞态，产品代码与守卫未削弱）。
- 提交链：#261 主体 5723d35 → #264 rag 竞态修复 ac62a3f（merge 6b6b4ab）→ #265 travel 竞态修复 e046aa7（merge c992708）→ 本文档收官 PR。
- 产物存档：`d:/tmp/ci-blindspots-main-full-41493314368/`（travel 失败证据）、`d:/tmp/ci-blindspots-main-full-41497402474/`（全绿 13 suite 统计）。
