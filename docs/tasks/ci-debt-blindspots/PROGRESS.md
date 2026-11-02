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
