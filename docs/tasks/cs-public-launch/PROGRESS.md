# PROGRESS — cs-public-launch（客服域公开投产实施）

- 任务文件：同目录 TASK.md（唯一事实源）；计划由 2026-11-02 会话立项（承接 cs-evaluation-upgrade 收官）
- 更新时间：2026-11-02
- 当前状态：**Q1 已合并；Q2 进行中，公开投产未验收**

## 阶段状态总表

| 期 | 主题 | 状态 | 分支 / PR | 证据 |
|---|---|---|---|---|
| Q1 | 上线巡检小件（管理端选项处理 / frontend-cs 构建验证 / 渠道资金落档 / 残留清理） | 已完成（残留按依据保留；共享管理端部署待协调） | feat/cs-prod-q1 / PR #259 | merge 01aa614；见下方验收日志 |
| Q2 | cs_faq BM25 索引补建 + live 冒烟对比 | 进行中 | feat/cs-prod-q2 | 宿主 remote 接线、60 条 live 冒烟中 |
| Q3 | live 质量缺口决策与实施（4 个拍板项） | 待实施 | 视拍板范围 | TASK §3 Q3 |
| Q4 | 多坐席容量压测 | 待实施 | feat/cs-prod-q4（计划） | TASK §3 Q4 |
| Q5 | 安全渗透自检（Mimosa + 边界复核） | 待实施 | feat/cs-prod-q5（计划） | TASK §3 Q5 |
| Q6 | 资金范围决策落档 | 待实施 | docs（计划） | TASK §3 Q6 |

状态取值：待实施 / 进行中 / 待验收 / 已完成 / BLOCKED（注明原因）/ PAUSED（注明现场）。

每期完成后回填：分支与 PR、实际测试命令与 PASS/FAIL、关键数字（如「系统繁忙」比例、压测 P95）、遗留事项。

## 环境

- Worktree：`D:\Program Files\workplace\agent-cs-prod`（新建，分支自最新 origin/main；worktree 无 .env 从主仓库拷贝）
- 测试解释器：`D:\Program Files\workplace\agent\.venv\Scripts\python.exe`（cwd 在 worktree 根）；真库 5433
- frontend-admin node_modules：junction 复用主仓库
- 宿主执行三件套（NO_PROXY / 注册表刷新 / 8088 模型服务）：cs-evaluation-upgrade PROGRESS 2026-11-02（六）
- 身份链（user:279 super_admin JWT + agent-auth-redis + X-API-Key 经 :9080）：cs-evaluation-upgrade PROGRESS 2026-11-02（四）
- 环境坑：cs-evaluation-upgrade TASK §2.3（Mimosa hook / GitHub 抖动退避 / compose 等价原命令 / MSYS_NO_PATHCONV / 并行会话纪律）——开工必读
- 关键参照：锁版数据集与门禁基线 cs-evaluation-upgrade PROGRESS 2026-11-02（三）；live 基线与移交清单 2026-11-02（六）；P7/P8 运营口径 2026-11-02（七）（八）

## 日志

### 2026-11-02（一）—— 立项

- cs-evaluation-upgrade P1–P8 全部收官（评测运营线达成受控试用口径）；用户拍板「写公开投产实施计划，下一会话处理」。
- 本任务立项：Q1–Q6 六期，覆盖管理端测评误导选项、frontend-cs 构建验证、渠道/资金落档、共享库残留清理、BM25 索引、live 质量缺口拍板、容量压测、渗透自检、资金决策。
- 门槛口径：Q1+Q2 = 受控试用放量；Q1–Q6 全绿 = 全量公开投产。

### 2026-11-02（二）—— Q1 接手与首轮验证（进行中，未验收）

- Worktree：D:/Program Files/workplace/agent-cs-prod；branch feat/cs-prod-q1；起点 HEAD 84601db97be7021a28d8375c7c16f973bb888d2f；当前改动未提交，无 PR。
- RunsTab 非 rag 隐藏评测方式、发送 offline、说明同步离线口径；customer-service.md 补 web 与资金开关投产要求。
- T0 git diff --check PASS；评测中心既有定向 Vitest 3 文件 / 8 tests PASS（其中 RunsTab 在 EvaluationCenter 测试中被 mock，不代表新行为验收）；管理端 build 编译成功，类型检查尚在运行。
- frontend-cs 从本工作树 compose build 成功（依赖与构建缓存命中）；仅 up -d --no-deps frontend-cs，未重建其他服务。容器 healthy，curl --noproxy '*' http://127.0.0.1:3300/login = 200。
- 共享 app 与 frontend-admin 来自 agent-wt-travel-proto，未覆盖。
- 清理冲突：review_candidate 禁止覆盖 approved/rejected，因此 398 approved 候选不能再次 reject；已向用户询问保留挂账或另补能力，未执行数据库删除。
- 新发现：共享 app 的 CS_BUSINESS_GATEWAY_MODE 实测为 http，CS_REFUND_REQUEST_MODE 未显式设置（代码默认 disabled）；与计划“双 disabled”不一致，尚未修改共享配置。文档描述为投产要求，不宣称当前运行态满足。
- 未完成：RunsTab 实际行为验证、admin build 最终结果/受影响服务重建、残留引用与活跃状态盘点及清理、资金运行态差异处理、PR/CI逐项核对/合并。Q1 与受控试用放量均未验收。

### 2026-11-02（三）—— 持续实施授权与计划校正

- 用户要求分析完整任务、优化实施计划并持续完成；持续目标已建立，TASK §7 为执行计划。
- 用户确认：398 approved 候选保留历史审核记录，过滤修复另挂小 PR。
- 新增 RunsTab 实际渲染测试 PASS：RAG→CS 隐藏选项/无在线说明、提交 offline、切回 RAG 保留选项；既有 8 tests PASS。
- admin 宿主 build exit 0，类型检查通过，但 standalone symlink EPERM 警告；Docker 生产构建在执行，尚未替换共享 admin 容器。
- 真库只读盘点：unanswered=29（日期 2026-10-04 至 10-31 UTC，并非已证实当日批次）；eval_bot tickets=60 open；confirmations=38 expired+2 cancelled；eval-cs-* checkpoints=0；archived case 关联 approved candidates=398。
- smoke_refuse_mvmdmqfr published version 有 1 item、2 个 suite version 引用，按禁止删除引用对象规则保留。其他同前缀空数据集仍需查权限与活跃关系。
- 业务网关 http 同时承接查询，disabled 不是受支持关闭模式；退款申请未显式设置，代码默认 disabled。文档已纠正语义，其他资金入口待 Q6 核对。
- 数据库 MCP 凭据失效；已使用项目 Python + 当前 .env 中 PG* 配置连接 5433，只读盘点成功，未执行共享库删除。

### Q1 残留清单核对补充

- 完整处置依据见 Q1-RESIDUALS.md：所有共享数据暂保留；60 工单并非无引用（29 handoff、41 条关联 open 会话），不执行孤立删除。29 未答记录不符合当日测试批预设；entry=16 的 intake 未删除原记录。
- 当前数据库所有含 tenant_id 的业务表中 govtest/p7v/p9g- 租户前缀命中为 0；不推广为所有数据库/对象名称均清零。
- 398 候选保留、published rag case 保留、confirmation 终态保留、目标 checkpoint 为 0，均已逐项记录依据。
- 本期为既有 UI 错误修复，新增组件测试由 evaluation-v2.yml 的评测中心目录测试自动覆盖；rag_smoke 无前端 Vitest 执行链，不新增平行前端门禁。
- 补充验证：评测中心目录全量定向 Vitest 5 files / 10 tests PASS（9.75s）；独立 tsc --noEmit exit 0；Diff 静态自查无后端/授权/数据库写入变更。
- Q2 只读预探：共享 rag-service 实际存在 /app/data/bm25/PUBLISHED.json 和索引文件，因此“缺失 BM25”的旧根因必须重新核实宿主与容器路径/发布指针，不盲目重建共享索引。
- admin Docker build PASS，镜像 sha256:4dc84edc5d72638472cab1677b0e4ccb4b0c3faf090e1dbe65b9c09ce3b75c88；独立容器 agent-cs-prod-q1-admin-check 在 :13200 运行，宿主 /login 与容器内 fetch 均 200。共享 :3200 未更新，避免覆盖旅游会话；后续统一部署仍需协调。
- Q1 PR #259，初始 HEAD 9c1ca42；自动 Review 提出 P2：不能将 travel/travel-provider 的离线质量评测标成 contract_only。已改为中性“离线评测（按方案执行）”，避免在前端复制后端模块能力注册表；补两旅游模块切换断言，定向测试 PASS。最终镜像需按修复后代码重建。
- RAG skip 归因实测：本 PR changed paths 输入 rag_smoke_path_filter.py --event pull_request 返回 relevant=false，所以 preflight 跳过；付费 Evaluation 仅可信 main push/dispatch，PR 跳过属预期。自动 Review 已计入，不额外触发云端审查。

### 2026-11-02（四）—— Q1 合并与 Q2 开工

- Q1 PR #259 已合并，merge SHA 01aa6149f69d942553da80189d27e0d3d16d90dd，PR HEAD e1268cbb2b5a1fced5ab4b3211cc518c14e23e19。
- 最新 HEAD CI 全部终态：Evaluation V2 free contract tests / Evaluation V2 admin build and tests / admin-observability-auth / frontend-admin-functional / frontend-admin-security / scan / Detect RAG changes / RAG Smoke 共 8 success；付费评测与 preflight 共 2 预期 skip，原因见前述路径/触发器核对。Review P2 已修复并定向复核，无已知未处置 P0/P1。
- 最新代码 Docker build PASS；镜像 sha256:adcc5c6a82aca9bab329c745b98fabca0a7ef1693881bb5e5c2ee933df8a823a 在独立容器 agent-cs-prod-q1-admin-check、:13200 运行；宿主 /login 与容器内 fetch 均 200。共享 :3200 仍为其他会话版本，不宣称已更新该入口。
- frontend-cs :3300 healthy、login 200；残留逐项保留依据见 Q1-RESIDUALS.md。上述为本机隔离验证，非全量投产通过。
- Q2 分支 feat/cs-prod-q2 自最新 origin/main@01aa614 创建。宿主默认 local、指向不存在的 backend/data/bm25；生产 remote 索引存在且 8090 ready。
- 共享 rag-service 被并行会话重建后现归 agent-wt-ci-blindspots；启动窗口首次检索超时，就绪后同目标 remote 检索成功（cs_faq，548 chars，index_status=ok），未改共享索引。
- 新 scripts/run_cs_live.py 提炼原一次性环境前置：强制宿主 remote、本机代理绕过、退款申请关闭、8088/8090 就绪检查、注册表刷新，然后调用既有 CLI；4 个入口测试 PASS，已加入 rag_smoke 免费预检。
- 本轮实际执行 60 条（v1 20 + CS2 faq12/query8/action8/complaint4/safety4/multi_turn4）；临时选样 d:/tmp/cs-q2-smoke.jsonl、日志 d:/tmp/cs-q2-smoke.log；run_id cs-prod-q2-1102，尚未完成，质量与成本暂不下结论。- Q2 补充验证：既有 cs runner live/离线相邻回归 8 passed（26.63s）；CS 决策回放门禁 6 passed（23.70s）；入口与 workflow 契约合跑 7 passed（0.46s）；py_compile PASS。60 条 live 尚在执行，进程会话 42710，禁止重复启动同 run。
- Q2 诊断批次新增环境缺陷：.env 的 BUSINESS_SERVICE_URL 使用 Docker 名称 business-service，宿主查询反复 DNS 失败。入口已固定宿主 8081 并增加 /health 检查，实际返回 200；修改后入口 4 tests PASS。现有批次不得作为完整链路验收，结束后用新 run ID 做一次必要复测，不并发启动。
- 成本归因限制：request_id=eval:cs-prod-q2-1102 在 llm_usage 为 0 行；远端 RAG 行为 eval_bot / rag-ask:*，另有空身份调用，日志出现子线程无请求预算状态警告。因此不能用该 request_id 零行宣称零成本或预算保护有效；后续以限定时间窗作上界并如实记录跨线程归因缺口。
- 首批已终态 completed、进程退出 1（质量门槛失败，非崩溃）：60 results、38 pass、final_answer 含“系统繁忙”0/60。因业务服务 DNS 缺陷，本批只作诊断证据，不作完整 Q2 验收。
- 修正后复测已启动：run_id cs-prod-q2-1102-r2，日志 d:/tmp/cs-q2-smoke-r2.log，运行会话 56166；复用同一 60 条选样。不得重启或与首批混算。
- r2 已完成：60 results、46 pass、0 error、繁忙0/60；8 query恢复，无退化，v1=9/20。整体质量门禁仍FAIL（smoke50%、core80.8%），CLI exit1。Q2知识链路指标达成，完整质量问题留Q3，不宣布公开投产。详见Q2-VALIDATION.md（含报告hash和成本归因限制）。
- 已新增 cs_public_launch_smoke 命名清单，只引用canonical ID/hash；选样构成与本次60条一致；入口+workflow契约8 passed。当前待Diff/PR/CI/合并。
