# PR-INTEGRATION-20261016 进度

更新时间：2026-10-17（Asia/Shanghai）
任务状态：IN_PROGRESS（#84 收口待合并；P2 价格治理/评测隔离/累计费用上界仍受外部条件阻断；#52/#66/#69 未合并）
主控分支：codex/pr-integration-20261016
主控 Worktree：C:\Users\wh\.codex\worktrees\pr-integration-20261016\agent
当前基线：origin/main = ad553d00cd63d18bbf9bef20506989c89fd8dff5（#84 Merge SHA）；主控任务树已合入 #82 基线

## G0 统一基线

| 门禁 | 状态 | 证据 |
| --- | --- | --- |
| G0-01 main/远端 | PASS（隔离任务树） | 最新 `git fetch origin --prune` 确认 `origin/main=e42975d6a2992f22bb4a95b9daf806dc3e67d2bf`（#80 Merge SHA）；原 main checkout `c5a2703` 与用户修改保持未触碰。 |
| G0-02 PR 状态 | PASS | 已核对 #73/#74/#75/#76/#77/#78/#79/#80 合并状态及 #52/#66/#69 当前 HEAD、Review、checks 和 skipped jobs；#50/#60 已关闭。#77 四条 Review 已解决；#78 合并后 #75 三条旧 Review 均已追加证据并解决；#79 五条 Review 已解决；#80 P1 文档 Review 已修复并解决。 |
| G0-03 Worktree 归属 | PASS | 控制树、#66、#69、#52 和历史 Follow-up 均为独立 worktree；本轮确认相关 PR worktree 干净后再施工，没有覆盖其他任务修改。其他无关 worktree 均未触碰。 |
| G0-04 避免覆盖 | PASS | 根目录 main checkout 中 frontend-admin/tsconfig.json、frontend-cs/tsconfig.json、frontend/tsconfig.json 的用户修改及未跟踪 .worktree-cleanup-archive-20261010/ 均保持不动。 |
| G0-05 依赖顺序 | PASS | 快速线 #73 → #74 → #76 → #77 → #78 已合并；价格巡检线 #79 → #80 已合并。RAG 线按 #66 → #52 顺序集成；真实质量评测仍需批准价格/预算和账本证据。#69 独立施工。 |

## GitHub 状态快照

| PR | 当前 head / base 与状态 | Review / checks | 施工状态 |
| --- | --- | --- | --- |
| #73 | MERGED；PR HEAD d9c4d7886bd2b63685721375b2b798137df61804；Merge SHA ed90609c335753f0519add64442cf7271d75ad54 | 三条 Review 已回复并解决；当前 HEAD 的代码检查通过；真实 API Evaluation SKIPPED | MERGED |
| #72 | 原 PR 已 MERGED，Merge SHA 45b0a7be54eaf0a3b7df4676f9978b095236932f | 两条遗留 Review 已通过 #74 的证据回复并解决 | MERGED（遗留由 #74 修复） |
| #74 | MERGED；PR HEAD 179d90ecf1adf87540707e5f4832a542df02d1f8；Merge SHA 5b372540800f1b6689d371e08e79fff009200425 | Trip URL/正式行程投影；本地与 GitHub 相关检查通过；真实 Provider/RAG Evaluation 未执行且不计通过 | MERGED |
| #70 | 原 PR 已 MERGED，Merge SHA 5df8b0603355566e7fa1eb87906129c11a8211ab | 原 PR Review 0；跨会话唯一性走新 Follow-up #76 | MERGED（原 PR） |
| #76 | MERGED；PR HEAD d47f43f10bd5914b0f13140d5daad18ae5b0e5b4；Merge SHA `7350cfe8dfb50bb6617bccb386603a5428e7747e`；后续 #77 将 main 推进到 `b3e2fb8` | 合并前共享项目库 duplicate-group 只读统计为 0；隔离真实 PostgreSQL 迁移/并发回归 1 passed；合并后在精确 Merge SHA 复跑隔离真实 PostgreSQL 1 passed。真实 API Evaluation skipped。098 尚未应用到共享数据库 | MERGED（共享库迁移状态仍待核实） |
| #69 | OPEN、Draft；HEAD `89d0e16ed643d88d6e9dc226c093d2e320eb81af`；base `e42975d6`；mergeState CLEAN | 当前 SHA 后端会话/身份/SSE/路由集 175 passed、授权 SQL/RAG/错误/SSE/Trace 集 216 passed；主前端 50 passed + 客服前端 30 passed，双方 `npx tsc --noEmit` PASS。最新 Detect、Secret Scan、免费 preflight、contract-check 均 PASS；真实 API Evaluation SKIPPED。已有旧 SHA 隔离付费 Doubao chat 成功，但完整授权 RAG/SQL/取消/Trace/费用 T3 未完成。额外断连测试在当前分支和 main 基线都以首帧 `log` 失败，属 main 基线测试预期问题 | BLOCKED（完成本地门禁；全链路真实模型/RAG/费用验收依赖只读价格 Registry 与双审/canary） |
| #66 | OPEN、Draft；HEAD `c4faac5ae073c5ff1616ccef31e7b82f22f4bf2f`；base `e42975d6`；mergeState CLEAN | 当前分支定向回归 126 passed、3 warnings；Detect、Secret Scan、免费 preflight、RAG Smoke gate PASS，真实 API Evaluation SKIPPED。早前隔离 testset PDF→Celery→IndexRun published→Active→授权 Search 已通过；与 #52 预算代理组合的 Worker/Search 结算证据仍有效但未在新合并基线上重跑 | BLOCKED（单独合并会绕过 #52 的预算结算保护；恢复与当前评测配置相容的 Registry 后再集成验收） |
| #52 | OPEN、Draft；HEAD `4665d4455b63e426fd6c6168712b1c99d4a62362`；base `e42975d6`；mergeState CLEAN；14 条 Review 已解决 | main 基线更新后路径过滤/费用/绑定定向集 128 passed；Detect、Secret Scan、免费 preflight、RAG Smoke gate PASS，真实 API Evaluation SKIPPED。外部只读价格 Registry secret 缺失；官方价格可查，但不能代替双人审核、24h canary 与权威 Registry | BLOCKED（需安全配置 `PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL` 只读副本并完成双审/24h canary；Provider Secret 名称存在于 `prompt-eval` 环境，未读取其值） |
| #50 | CLOSED | FE-4 已被 PR #72 覆盖；当前 main 对应三组定向前端测试 40/40 通过 | 已关闭过期 Draft |
| #60 | CLOSED | 文档中 T1 已由 PR #61 合入；独立作品集交接材料由 #75 交付 | 已关闭旧 Draft |
| #75 | MERGED；PR HEAD dba170373c4663919ab9b0e2b87b5f800d479310；Merge SHA 04846a61f889fa561b9c2d2184cd196c93e75dd5 | 合并后 3 条 Review 由 #78 修复；Follow-up 合并后已追加 Merge SHA 与验证证据并全部解决原线程 | MERGED（Review 收口完成） |
| #77 | MERGED；head `1093c39beb23b00d4210660eb877d062d49230e5`；Merge SHA `b3e2fb85937d011179ed046b6d264a7a0f3454d4` | 本地 42 passed，四条 Review 已修复/解决。当前 SHA Detect、Secret Scan、PR 免费 preflight 与 8-case Smoke 均 PASS；真实 API Evaluation skipped。main push 的 8-case Smoke 和 Secret Scan PASS，未运行付费 Probe | MERGED |
| #78 | MERGED；PR HEAD `4e1216884333917e392b3442c826500ce86981cd`；merge 前 base `b3e2fb8`；Merge SHA `e4881940f1d39f82d1f7a938562e0e611f6677bb` | 文档相对链接、源码路径和 diff-check 通过；PR Smoke/Detect/Secret Scan 与 main push 的 RAG PR Smoke/Secret Scan PASS；paid/preflight job 按文档工作流跳过；当前 Review threads 为空 | MERGED |
| #79 | MERGED；PR HEAD `648c78c209ea1a369497972ea3158620caebe882`；Merge SHA `333efc650deed0d67868b5f8b2833063b5960daa` | 管理端价格巡检页面、只读 API、Celery 每日官方价抓取与运行历史；48 backend passed、1 frontend passed、TypeScript/官方页解析通过；五条 Review 已解决 | MERGED |
| #80 | MERGED；PR HEAD `4bf75571b3fc7ed53f1eca290dcf9e9fcd17765d`；Merge SHA `e42975d6a2992f22bb4a95b9daf806dc3e67d2bf` | RAG Smoke 路径过滤回归 12 passed；main push 最终 gate PASS，价格巡检变更判为 irrelevant，付费 RAG job 按路径安全跳过 | MERGED |
| #81 | MERGED；PR HEAD `f1ca05cb95d13aec9bd837fab3bffb703bc81a6f`；Merge SHA `89315c9d32b383d31dd390d7a90dbe6b9c4eb75b` | #77 价格治理、评测隔离与 CI 路径过滤的 Follow-up；定向 84 passed、隔离 PostgreSQL 验证、无 Provider 调用 | MERGED |
| #83 | MERGED；PR HEAD `64a22ab`；Merge SHA `e354f3b3a6517f70c70856627661118f4da6c933` | #73 模型健康 Follow-up（探测结果与运行时语义对齐）；后端 43、前端 60 定向通过 | MERGED |
| #82 | MERGED；PR HEAD `53edac1`；Merge SHA `0bc9038673632f46b7c2fdb10e714259e9e836b6` | #79 价格巡检 Follow-up：告警恢复、任务取消/超时状态、高档价格门禁；隔离 PG 4 passed、价格档位/任务中断 6 passed、管理端 3 passed；真实 8-case SKIPPED | MERGED |
| #84 | MERGED；PR HEAD `29ae19ddcecc5c148002cde954f3cd4b0046eb09`；Merge SHA `ad553d00cd63d18bbf9bef20506989c89fd8dff5` | 合入最新 main、迁移重号修复（100→101）、`sk` 标签式脱敏、catalog 断言修复；PR 与 main-push 的 Detect/Secret Scan/preflight/RAG gate 均 PASS；隔离 PG travel_v2 176 passed、前端 travel 62 passed、tsc PASS；真实 8-case SKIPPED | MERGED |

原 PR branch protection 核实为 Required Checks 为空、required approvals 为 0；此次未绕过保护。Skipped job 不计为通过。

## 合并历史

- #73 普通 merge：ed90609c335753f0519add64442cf7271d75ad54。
- #74 普通 merge：5b372540800f1b6689d371e08e79fff009200425。
- #75 普通 merge：04846a61f889fa561b9c2d2184cd196c93e75dd5。
- #76 普通 merge：7350cfe8dfb50bb6617bccb386603a5428e7747e。
- #77 普通 merge：b3e2fb85937d011179ed046b6d264a7a0f3454d4；后续 #78 将 main 推进至 e488194。
- #78 普通 merge：e4881940f1d39f82d1f7a938562e0e611f6677bb。
- #79 普通 merge：333efc650deed0d67868b5f8b2833063b5960daa。
- #80 普通 merge：e42975d6a2992f22bb4a95b9daf806dc3e67d2bf。
- #81 普通 merge：89315c9d32b383d31dd390d7a90dbe6b9c4eb75b。
- #83 普通 merge：e354f3b3a6517f70c70856627661118f4da6c933。
- #82 普通 merge：0bc9038673632f46b7c2fdb10e714259e9e836b6。
- #84 普通 merge：ad553d00cd63d18bbf9bef20506989c89fd8dff5（当前 origin/main）；main tree `c894bac` 与 PR HEAD tree 完全相同。
- #72 与 #70 原始合并历史保留；后续修复均使用 Follow-up，没有改写旧历史。
- #50/#60 已关闭，未将过期 Draft 合入 main。

## 施工线状态与下一步

| 线 | 当前状态 | 下一项实际动作 |
| --- | --- | --- |
| A Provider/旅游/客服 | #73/#74/#76 MERGED；#75 Follow-up #78 文档修复完成 | #78 的自动 Review summary 没有公开具体意见，未据此猜测修改；该 PR 已按已确认门禁合并。098 已进 main，仍需在获授权发布环境执行迁移。 |
| B RAG | #66 数据终态已验收；#52 价格门禁 BLOCKED；#77 MERGED | #66 Worker E2E 已通过，但其分支独立合并会使 Worker 调用缺少 #52 的有效预占/结算，暂不单独合并。#52 当前 HEAD 已修正 usage 与预算请求关联；8-case 真实评测仍受价格治理和只读 Registry 外部条件阻断。 |
| C 主助手 | #69 BLOCKED | 当前 SHA 后端/前端定向回归和免费 CI 全 PASS；还缺真实授权 RAG/SQL、全链路 SSE/Trace/费用验收。必须通过 #52 权威价格 Registry 和治理门禁后再运行付费链路。 |
| 过期 PR | #50/#60 已关闭；#75 已合并 | #75 三条新增 Review 由 #78 修复；合并前暂不解决原线程。 |
| main 集成 | BLOCKED | main `e42975d` 的局部 CI 有证据，但未部署/启动精确 SHA 完整栈，MAIN-01～34 不作整体 PASS；#52 Provider API Secret 名称存在，权威只读价格 Registry 与双审/24h canary 缺失。 |

## 外部阻断与诊断

### #76：退款订单唯一性迁移

- PR #76 已普通合并，PR HEAD d47f43f10bd5914b0f13140d5daad18ae5b0e5b4，Merge SHA 7350cfe8dfb50bb6617bccb386603a5428e7747e。迁移 098 已注册进 main；部署数据库是否应用仍未知。
- 合并前对项目数据库以只读事务执行 duplicate-group 计数，结果为 0；未修改共享数据库。不能把此结果解释为迁移已应用。
- 隔离 PostgreSQL 在当前 Merge SHA 上真实回归 1 passed（CS-12）；migration registration、唯一冲突/幂等/租户隔离等已有定向测试在 PR 合并前通过。
- 发布前需由有权限的操作流程在目标环境检查 098 应用状态并执行迁移；本任务不写共享/生产数据库。

### #66：PDF Worker 真实终态

- 当前 PR HEAD `ea6eab9ebe5e18237cbaa1a0a57eacf8a89f115b`，包含 PDF 页码清洗、Worker 追踪、递归分块边界空格和本轮内容覆盖修复；已同步 main `e488194`。页面数字保留回归 3 passed；Worker/运行时/存储集 44 passed；上传/索引/Celery 定向集 36 passed；覆盖路由/质量门禁集 27 passed；改动文件 py_compile、diff-check 和定向 Ruff 通过。该 SHA 的 Detect/Secret Scan PASS，RAG Smoke preflight 仍在运行；真实 API Evaluation skipped。目标上传 `b96f4d86ebe3`、IndexRun `f7a06549-ba71-4abf-a134-3a7b3d35deb1` 在只读隔离库中未终结，两个快照的 stage 不一致，不能推断实际终态。Celery active/reserved/scheduled 为空。原 Worker/Celery/Docker SIGKILL 事件日志不可用，不能断言 OOM。
- 当前 Docker Desktop 可用；主 `agent` Compose 有 16 个共享服务运行，容器总内存约 3.1GiB/8GiB，RAG worker 上限 3GiB。启动完整隔离解析链可能使内存接近上限，未启动以免影响共享任务。
- 只读启动并停止旧 `rag-pr55-e2e-evidence-db`：容器无网络/端口/bind mount，独占独立 evidence-copy 卷；该库只有 22 个其他 IndexRun，不含目标 upload；日志只有 PG 启动与诊断 SQL。另一个旧隔离源卷 `rag-pr55-e2e_pg_data` 的只读副本包含目标 upload/IndexRun。原 worker 容器已不存在，故无法恢复 SIGKILL 信号、memory event 或进程日志。
- RAG-03 PDF 最终 IndexRun、RAG-08 Search 未完成；RAG-04/09 部分或未验证；RAG-12 未验证。PDF 清洗修复提交 `96407e7` 是早期分支提交而非当前 HEAD；该改动仍在当前 PR HEAD 的提交祖先中。
- 对 `rag-pr55-e2e_pg_data` 按 Compose 标签确认属于旧隔离项目，原卷未被活动容器挂载；只读副本两次快照均显示目标 upload `b96f4d86ebe3`、IndexRun `f7a06549-ba71-4abf-a134-3a7b3d35deb1` 未终结，但 stage 分别为 `table_describe` 与 `indexing/index`。generic task `0f567e80-e6e7-45e1-aaa4-c981f104e0a6` 为 RUNNING，Celery ID `737ad57f-b12c-4cdf-b7ea-8741decc7f93`，worker 为空，recovery_count=0，租约已过期；Redis 持久化卷中找不到该 Celery ID。原 Worker/Beat 容器、SIGKILL、memory event 与日志均已丢失，不能归因为 OOM 或断言具体退出原因。
- 本轮确认的代码缺口：上传路由在 Celery `apply_async` 成功后只回填 generic `tasks`，没有回填 `rag_index_runs.celery_task_id`；Worker 租约认领固定传 `worker=None`。已在 #66 修复并推送 `f3573f0c68f41a28a076b36d5bc0be1bbdbd4236`：运行记录回填 Celery ID，普通索引和重索引都把 Celery hostname 传到既有租约字段；回填失败记日志且不重复投递。
- PR #66 HEAD 定向运行 `test_rag_upload_celery_mode.py`、`test_index_task_runtime.py`、`test_upload_failures_store.py` 共 44 passed，使用隔离 PostgreSQL 副本；py_compile、diff-check 通过。Windows Proactor teardown 有 2 条 warning。恢复并发单测 `test_case_d_concurrent_sweep_single_recovery` 单独重跑 1 passed；两文件恢复套件的一次整组执行未得到 pytest 汇总，不计为通过。临时副本容器/卷已清理，源卷仍保留。

#### 2026-10-16 最新 HEAD 验收更新

- 当前 #66 HEAD 为 `3a2ddf91d64cdd148379e1e07abe9d75d8e90091`，GitHub 该 SHA 的 Detect、Secret Scan、RAG Smoke preflight 与 smoke wrapper 均 PASS；真实 8-case API Evaluation 为 SKIPPED（不计通过）。隔离任务数据库 `agent_rag_e2e_memory`、Redis `pr66-worker-e2e-20261016-*`，测试文件基于授权 `source_text.pdf` 的临时副本；原件未修改。
- #66 当前 SHA 单独真实 E2E：upload `ec61279f894a`，Celery task `743cbf6f-22ec-4f7f-90dd-d79b0d265173`，IndexRun `published / publish`，Registry `active`、42 chunks；授权 Search HTTP 200、10 条结果并命中当前文档唯一标记。模型角色 Registry 从 DB 加载（4 providers / 9 models / 10 credentials / 7 role overrides），实际调用付费 Qwen/Doubao Provider；该 #66 独立运行没有通过 #52 预算代理结算，不能据此证明费用闭环。
- 将 #66 HEAD 与 #52 当前 HEAD `91db7640` 在临时 detached Worktree 组合，未改动任一 PR 分支。集成 E2E upload `1027ab7c148e`，IndexRun `published / publish`、Registry active、42 chunks；Search HTTP 200 并命中当前 PDF 的 `SENTINEL-OCR-P01`，doc_id 与本次文档一致。Worker 的 3 次 Qwen embedding 调用共 3,949 tokens，预算以 upload request_id 预占并结算 CNY 0.007836（12 个 user/tenant×day/month scope rows）；Search 的 1 次 5-token 调用亦有请求级预算，usage 与 reservation request_id 一致。该次文档 `llm_used=0`，没有 Doubao metadata 调用。
- 隔离账本中该合成 user 的历史验证合计 33 次 Provider 调用、57,692 tokens、`billed_cost_cny` 合计 0.182506、无 `unknown` 行；这是现有 Registry 快照计价，不是供应商账单。按官方刊例价重算约 CNY 0.0383713，仍为列表价估算。现有 Registry 的 Doubao 行以 USD 0.2/2.0 per 1M 记录；火山引擎官方 Ark 在线推理对 `doubao-seed-2-0-mini-260428` 输入≤32K 为 CNY 0.2/M 输入、CNY 2/M 输出；Qwen3.7 embedding 官方价为 CNY 0.0005/1K。来源：[火山引擎模型计费](https://docs.volcengine.com/docs/LakeAIService/Largemodelbilling?lang=en)、[阿里云 Qwen Embedding 计费](https://help.aliyun.com/en/model-studio/text-embedding-synchronous-api)。
- 直接 UPDATE 隔离 `model_price` 被 append-only trigger 拒绝并回滚。价格治理要求不同审核人双审、24 小时 canary，不能把旧错误币种行当精确价格，也不能伪造 reviewer。GitHub `prompt-eval` 当前 run 的免费 preflight PASS，付费 8-case job SKIPPED；没有使用假价格或绕过 CI 门禁。
- PR #52 新发现并修复 usage.request_id 与预算 reservation request_id 不一致：Worker 局部 Trace ID 曾覆盖稳定 upload_id。提交 `91db7640cd83828af93aeeccdda65fa3b960a85c` 优先使用可信预算 request_id，trace_id 保持独立；新增两者不同的回归测试。定向集 34 passed，py_compile/diff-check 通过。提交已推送，当前 SHA Detect/Secret Scan/preflight/smoke PASS，真实 8-case Evaluation SKIPPED；Codex 自动 Review 仍针对旧 SHA `48675a4`。

### #69：主助手真实 T3

- PR HEAD `bf857fe8cb972f6d3cb83ff63f01734833dcdee3`，基于 main `e488194`。在隔离 Next BFF→APISIX→FastAPI 链路上确认 Runtime Registry 来自 DB：`main` 角色绑定 `doubao-seed-2.0-mini`，Provider 为付费 Doubao；“缺少本地 Doubao”不是根因。一次真实中文 chat 成功，usage 记录身份为测试 user/tenant，325 tokens、估算账本成本 CNY 0.003110（供应商实际账单未核对）。未触碰共享数据库。
- 实际请求 Trace 中发现并行 `inventory_alert` workflow 曾借用请求线程上的全局 `_thread_current`，将不相关 SQL/Reporter spans 附加到 chat Trace。修复已提交并推送 `bf857fe`：ContextVar 优先，线程局部仅供同步代码回退；asyncio task/事件循环回调无 ContextVar 时 fail-closed；后台并行线程须显式 `bind(trace)`；Span 完成按创建时所属 Trace 通知 listener。同步更新 workflow/capability 调用点和夹具。
- 当前 HEAD 定向测试合计 119 passed；包含 TraceCollector 57、Trace 修复 16、workflow executor 24、隔离线程/任务/事件循环回调回归 4，以及 token/version provenance、RAG claim rejection 和 capability 定向测试。临时隔离 PostgreSQL 测试容器已停止。py_compile、diff-check、tracer 与新回归测试 Ruff 通过。
- 当前 GitHub HEAD `bf857fe` 的 RAG Smoke、free preflight、contract-check、Detect 与 Secret Scan 全部 PASS；真实 API Evaluation SKIPPED。此前真实单次 chat 不验证完整 Memory、多租户隔离、授权 RAG/SQL、SSE resume、副作用取消、Trace/费用闭环或安全失败终态；AG-08 部分通过，AG-09 有一次实际模型回答，AG-10～20 仍需按链路补验。PR 保持 Draft，不能据单次 chat 宣称 T3 完成。

## 施工证据

### #73 Provider/OCR

- 本地 37 个后端定向测试、前端 19 个测试及 TypeScript 检查通过；py_compile、定向 Ruff、diff-check、secret scan 通过。三个 Review 修复并解决。普通 merge 已进入 main；未发起额外付费 Provider 调用。

### #74 Trip 恢复与正式事实

- 创建成功后 trip_id/conversation_id 写入 URL，恢复失败保持只读；正式 Trip allowlist 投影预算 6500 字符，过滤 PII/凭据形态并将备注标记为不可信，普通问答不改 Revision。
- 前端 Vitest 23 passed、TypeScript 通过；后端 API 契约 12 passed；loopback 隔离 PostgreSQL 属主校验 1 passed；py_compile/diff-check/secret scan 通过。#72 两条遗留 Review 已回复并解决；#74 普通 merge 已完成。

### #75 客服作品集交接文档

- 核对 #60 的旧 HANDOFF 状态过期，但作品集演示、设计说明、评测和生产边界资料仍有独立价值。PR #75 新增最小交接文档/计划，修正退款政策状态及不存在的运维脚本引用。
- Markdown 相对链接核对、diff-check 通过；退款政策定向单测 32 passed；GitHub scan、Detect RAG changes、RAG Smoke PASS。普通 merge SHA 04846a61。合并后发现的 3 条 Review 已由 #78 修复；#78 合并后均追加证据回复并解决。

### #77 RAG Smoke 触发与计费保护

- 当前 PR HEAD `1093c39beb23b00d4210660eb877d062d49230e5`；四条 Review 均有修复提交、逐条回复并已解决。
- 计费迁移过滤加入 `budget`/`billing`，测试包含迁移 066 正向和退款迁移 098 负向；模型/组件所有价格行必须共享同一版本和来源；移除 Actions Variables 自报批准 JSON，改从受保护 Secret 指向的 PostgreSQL 只读 recovery 副本读取现行 Registry 行；两条付费 workflow 都在任何 Provider Probe 前导入并执行价格校验。现行运维文档已记录配置及失败关闭语义。
- 定向 pytest 42 passed（1 个既有 collection warning）；py_compile、Ruff、diff-check 和 tree Secret Scan 通过。PR 与 main push 的 Detect/Secret Scan/free preflight/8-case gate 均通过；真实 API Evaluation 按信任边界 skipped。合并 push 路径本地判定 `relevant=false`。未配置外部 Registry secret，未运行 Provider Probe；实际付费价格链路仍未真实验收。

### #76 退款申请跨会话唯一性

- 分支 codex/pr70-refund-unique，PR HEAD d47f43f10bd5914b0f13140d5daad18ae5b0e5b4。PR #76 已普通合并，Merge SHA 7350cfe8dfb50bb6617bccb386603a5428e7747e；098 已注册进 main，目标共享数据库实际应用状态未核实。
- 新迁移 098 只读统计历史活跃重复组，再建 tenant/user/规范化订单 ID 的活跃退款部分唯一索引；原会话级索引保留。空/缺失订单键不绕过索引。应用层拒绝空白 ID，保留预检友好消息；数据库冲突后重新查询确认 ID、订单、租户、用户、会话，跨会话同订单冲突不返成功。同一确认同 payload 复用，payload 不同冲突；结案后允许按资格重新申请；commit outcome 不明不自动新建第二申请或显示退款成功。
- 合并前对项目数据库 duplicate-group 只读计数为 0；没有写共享库。Loopback 专用 PostgreSQL 验证空/空白键被唯一索引阻止、跨会话并发恰为一成功一业务冲突、不同用户/租户互不阻断、结案后可按资格重申。合并后精确 SHA 的真实 PG 测试 1 passed。
- Service/guard/store/idempotency/迁移/API 测试共 94 passed、20 skipped（既有条件 skip）；commit unknown 单测在该集合中；py_compile、改动测试 Ruff、diff-check、tree/diff secret scan 均通过，secret scan 0 findings。全文件 Ruff 还报告若干不在本次改动行的历史告警，未扩大修改范围。
- 共享数据库迁移执行仍未验证；发布时需由授权流程检查并应用 098。不得把合并或只读重复组数量当成数据库迁移已运行。

### #52 MultiQuery 与费用

- 分支已同步 main `e488194` 并推送，当前 PR HEAD `c37b0ed`；PR Open/Draft/CLEAN，保留原功能改动。14 条 Review threads 全部已解决。权威价格只从只读 Registry 获取，删除旧 JSON 自报价格入口；保留 MultiQuery 绑定、请求预算和账本验收门禁。
- 代码 HEAD `8297b0f` 的集成冲突相关定向测试 45 passed；当前 HEAD 的 GitHub preflight、Detect、Secret Scan、required `RAG Smoke (8 cases)` 均 PASS；真实 API Evaluation 为 SKIPPED_UNTRUSTED_PR。仓库 `prompt-eval` 环境虽有 Provider secret 名称，但没有 `PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL`；也没有可读取的批准价格/预算快照，因此未执行真实评测。
- 当前 HEAD 的真实检索质量和费用账本未验证。旧 SHA Top-1 0.7143 低于 0.80，不能作当前结果。当前无可恢复的已批准价格快照与评测预算，未授权再次发起 Provider 调用；等待价格与预算门禁后再执行真实评测。

## main 集成验收状态

当前 main：e42975d6a2992f22bb4a95b9daf806dc3e67d2bf。已合并 #73、#74、#75、#76、#77、#78、#79、#80。MAIN-01～MAIN-34 总体尚未进行整体验收；已验证的局部链路仅按各 PR 精确 HEAD/merge run 记录。完整基础设施、业务链路、共享数据库迁移、主助手真实 T3 与 RAG 真实质量仍未验证，不作上线就绪结论。

## 最近检查

- 原 main checkout 的用户变更和未跟踪目录保持原样。
- 控制工作树每次先核对 diff/status；不做全局 cleanup。
- 最近定向验证：#66 当前 HEAD 的上传/索引/Celery 集 36 passed、覆盖路由/质量门禁 27 passed、Worker/运行时/存储集 44 passed、页码保留 3 passed；#69 晚到事件隔离测试在 b3e2fb8 与 e488194 基线分别 1 passed，此前事件作用域回归 106 passed；#76 精确 Merge SHA 的隔离 PostgreSQL 回归 1 passed；#73/#74 后端 API/健康契约 37 passed（仅测试令牌）。#52 代码 HEAD 的付费预检、模型绑定、预算、工作流和路径过滤测试 45 passed。#66 新 SHA 的 Detect/Secret Scan PASS，RAG Smoke preflight 仍运行；#52/#69 以此前已核验 SHA 的免费 CI PASS，真实 API Evaluation 均 skipped。#66 真实 PDF E2E/Search、#69 真实隔离 T3、#52 真实检索与账本尚未完成；#76 迁移尚未应用于共享数据库。


## main 集成验收逐项矩阵（当前证据）

状态按当前 main e42975d 及本轮实际证据记录；PR 分支上的通过不替代 main 部署或真实 E2E 结果。

| ID | 检查项 | 状态 | 当前证据 / 缺口 |
| --- | --- | --- | --- |
| MAIN-01 | APISIX 正常 | BLOCKED | 共享 Compose `127.0.0.1:9080/health` 返回 200 并到达 FastAPI；运行镜像 build commit 为 unknown，不能算当前 `origin/main` 验收。 |
| MAIN-02 | FastAPI 正常 | BLOCKED | 共享 Compose `127.0.0.1:8000/health` 返回 200；镜像无可核对 commit，真实服务不对应当前 main SHA。 |
| MAIN-03 | Next.js 用户端正常 | BLOCKED | 主控隔离树无 frontend/node_modules；不重复安装，未启动页面。 |
| MAIN-04 | 管理端正常 | BLOCKED | 未启动管理端；当前任务树无已装前端依赖。 |
| MAIN-05 | PostgreSQL 与 Redis 正常 | NOT VERIFIED | `agent-postgres-1` 的 `pg_isready` 成功；`agent-redis-1` 容器 healthy，但未认证的 `redis-cli ping` 返回 `NOAUTH Authentication required`，未把它当作 Redis 故障。未做当前 main 服务链验收。 |
| MAIN-06 | Celery Worker、Beat 和消费 | BLOCKED | agent/rag-index/metadata/report/maintenance Worker、Beat 与客服 dispatcher 容器显示 healthy；未对隔离当前 HEAD 的真实任务投递与消费做验收。 |
| MAIN-07 | 健康及就绪接口 | BLOCKED | 共享服务 `/health`、RAG `/healthz` 返回 200；RAG `/readyz` 返回 503 `SERVICE_NOT_READY`（提示 embedding Provider API Key 不可用）。RAG 镜像创建于 2026-10-13、build revision unknown，容器内 `embedding_singleton.py` 与当前源码 SHA256 不同，因此不将其作为当前 main 的代码失败或 PASS。 |
| MAIN-08 | Prometheus / Trace 核心观测 | NOT VERIFIED | 未启动观测栈或采集真实请求 Trace。 |
| MAIN-09 | 用户正常聊天 | BLOCKED | 无安全注入的隔离 BFF、Provider 与服务链。 |
| MAIN-10 | 一般概念问答 | BLOCKED | 真实 LLM 未调用；未用假响应作质量结论。 |
| MAIN-11 | 多轮上下文 | NOT VERIFIED | #69 当前基线局部测试通过，但该 PR 未合并，未做实时聊天。 |
| MAIN-12 | RAG 可信引用 | BLOCKED | 未取得当前 main 上完整 Active 索引和授权 Search 的真实证据。 |
| MAIN-13 | SQL 授权查询 | BLOCKED | 无隔离真实服务/资源权限端到端环境。 |
| MAIN-14 | 不跨租户、用户或产品泄露状态 | FAIL | 当前 main 尚未含 #69 的会话事件作用域修复；修复分支局部 106 项通过，仍未合并。 |
| MAIN-15 | SSE 取消、错误与恢复 | NOT VERIFIED | 局部契约测试有证据，未完成当前 main 的真实服务流验收。 |
| MAIN-16 | 新建 Trip | NOT VERIFIED | Follow-up 针对 URL/恢复与投影有测试；完整实时创建链未验收。 |
| MAIN-17 | 已有 Trip 恢复 | PASS | #74 合并；前端定向测试 23 passed，API 契约 12 passed，隔离 PG 属主校验 1 passed。 |
| MAIN-18 | 聊天理解正式行程 | BLOCKED | 正式行程结构投影已测；真实模型回答未运行。 |
| MAIN-19 | 草案与正式版区分 | PASS | #74 前端投影定向测试覆盖正式状态/草案区分；真实 LLM 表述未验证。 |
| MAIN-20 | 分享和 ICS 不受影响 | PASS | main 上对应前端定向测试 40/40 通过。 |
| MAIN-21 | 普通问答不修改正式行程 | PASS | #74 投影/问答状态测试覆盖 Revision 不变；真实模型链未验证。 |
| MAIN-22 | 订单资源授权正确 | NOT VERIFIED | 未运行当前 main 的真实客服 API 与资源授权集成。 |
| MAIN-23 | 退款申请需要确认 | NOT VERIFIED | 本轮测试了 Follow-up 分支的案件状态，不把未合并分支证据算作 main 通过。 |
| MAIN-24 | 待确认申请使用版本 CAS | NOT VERIFIED | 未在当前 main 上执行真实并发/版本更新 API 验收。 |
| MAIN-25 | 同订单跨会话不重复登记 | BLOCKED | 修复和迁移 098 已合并；精确 Merge SHA 的真实隔离 PG 并发测试通过，项目库只读重复组计数为 0；共享目标库迁移应用状态未核验，本任务不执行共享写入。 |
| MAIN-26 | 申请登记不冒充资金退款成功 | PASS | #76 已合并并在定向服务/API 契约与真实 PG 回归中验证案件申请不等于资金退款完成；完整客服端到端仍未验证。 |
| MAIN-27 | 人工接管与失败状态正确 | NOT VERIFIED | 未运行客服 Worker/API 端到端。 |
| MAIN-28 | 多格式文件内容完整 | FAIL | main 尚未含 #66 清洗修复；当前 PDF 短数字页码歧义问题仍待 Follow-up 合并。 |
| MAIN-29 | 上传索引具有真实终态 | BLOCKED | 只读隔离数据库副本仍显示目标 IndexRun 为 running、stage 快照不一致且无 finished_at；IndexRun 自身 Celery ID 为空，但 generic task 行有 ID `737ad57f…`。无 worker hostname、源码 SHA、原始事件或最终 Search 证据，不能宣称终态。 |
| MAIN-30 | Chunk / Parent 元数据正确 | NOT VERIFIED | PR #66 分支定向测试通过；main 完整索引/Search 未验收。 |
| MAIN-31 | 检索遵守授权 | BLOCKED | 无当前 main 的真实 RAG 检索服务链验收环境。 |
| MAIN-32 | 模型健康检查状态正确 | PASS | main 定向健康/API 合同套件 37 passed；测试使用临时测试内部令牌，未使用真实凭据。 |
| MAIN-33 | Token 与费用可归因 | BLOCKED | #52 评测账本/批准价格快照不可用。main-push RAG Smoke 曾意外执行 2 次 `unpriced` Provider 调用（2,335 tokens，金额未知），8-case 未运行；#77 已合并付费前价格门禁，但不能把历史探测当费用验收证据。 |
| MAIN-34 | 已合并质量门槛有证据 | BLOCKED | 当前 RAG real API evaluation 为 SKIPPED；不计通过。 |

## 本轮 main 定向验收补充

- 后端命令：pytest backend/tests/infra/test_ocr_model_health.py backend/tests/api/test_sys_model_roles_api.py backend/tests/api/test_sys_model_health_api.py backend/tests/travel_v2/test_api_contract.py -q。
- 初次无凭据执行出现 test_requires_admin 503（1 failed、36 passed）；根因是本地未设置 AI_INTERNAL_TOKEN，fail-closed 依赖按设计拒绝且返回 503。使用进程级临时测试令牌 integration-test-only 重跑后 37 passed，环境变量仅对子进程生效，不涉及真实凭据。
- 前端在隔离主控树执行 npm test -- ...TravelChatPage.test.tsx ...TravelV2TripConversation.test.tsx 时，命令失败：vitest is not recognized；npx tsc --noEmit 无本地 TypeScript 安装。未安装依赖、未触碰原 main checkout 的 ignored node_modules，前端 post-merge 重跑保持 BLOCKED；合并前 PR #74 的 23 个前端测试证据仍可追溯。

## 2026-10-16 当前续工状态

- 在控制 Worktree 执行 `git fetch origin --prune` 后，origin/main 为 `e4881940f1d39f82d1f7a938562e0e611f6677bb`；控制树已合入该基线。原 main checkout `D:\Program Files\workplace\agent` 仍有用户修改，保持未触碰。
- PR #77 已正常合入，Merge SHA `b3e2fb85937d011179ed046b6d264a7a0f3454d4`。PR SHA `1093c39` 的 free preflight、RAG Smoke、Detect、Secret Scan 全部通过，真实 API Evaluation skipped；main push 的 8-case Smoke 和 Secret Scan 也通过，未运行付费 Probe。未配置 Registry Secret，因此没有真实价格/Provider 验收。
- PR #69 当前 HEAD `a06ebe1815d7f4a97b6c5eff27029c20bfddebc9`，已包含 main `e488194`。晚到 memory event 隔离测试在同步前后均 1 passed，旧事件作用域集 106 passed；新 SHA 的 contract-check/preflight 仍运行。真实 Provider、隔离服务与权限/Trace/费用 T3 仍未验证。
- PR #66 当前 HEAD `ea6eab9ebe5e18237cbaa1a0a57eacf8a89f115b`，已包含 main `e488194`。新修复将 Parent/Leaf 分开 SimHash 去重，并保留有源身份的短 Leaf，防止质量门禁之前丢失有效正文。上传/索引/Celery 36 passed、覆盖路由/质量门禁 27 passed；Detect/Secret Scan PASS，preflight 仍运行；真实 API Evaluation skipped。目标旧 Worker 日志和 memory events 不存在，不能还原 SIGKILL 原因；真实 PDF E2E 仍未执行。
- PR #52 当前 HEAD `c37b0ede41ca9b5667e9188eb0db0722ff10d073`，已同步 main `e488194`；14 条 Review 全部已解决，代码集成定向测试 45 passed。前一 SHA 的免费 checks 全部 PASS；新文档基线 SHA 的 CI 仍待更新。真实质量评测和账本仍需获批价格/预算快照；不发起付费调用。
- PR #78 已合并为 main `e488194`；Follow-up 修复的三条 #75 原始 Review 已附合并 SHA/验证证据并全部解决。

### RAG Smoke 意外真实 Provider Probe 事件

- PR #76 合并触发 main-push RAG Smoke 工作流 `38787751555`；旧路径过滤把普通迁移 `098_cs_refund_order_unique.sql` 当成 RAG 变更，且旧 workflow 在付费 Probe 前无批准价格快照门禁。尝试取消时 Probe 已执行；之后 fixture 导入取消、Index/Search/8-case 评测 skipped，整个工作流为取消/失败，不能记为 PASS。
- 脱敏账本汇总显示 2 次 Provider 调用、共 2,335 tokens，均为 `unpriced`，`billed_cost_cny` 为 NULL，实际金额未知。不能声称没有 Provider 调用或声称已知费用；完整 8-case 质量评测没有运行。此次没有再触发 Probe。
- 后续 #77 区分 push 与 PR 静态变更，并在任何模型 Probe 前验证 Registry 批准价格。HEAD `1093c39` 本地路径判定为 `relevant=false`；GitHub 免费 PR 检查和 main push 的 8-case Smoke / Secret Scan 均通过，真实 API Job skipped。该修复已随 `b3e2fb8` 合并。

### 当前阻断和下一项施工动作

- #75/#78：Follow-up #78 已合入 main `e488194`；三条旧 Review 均已追加证据并解决，收口完成。
- #77：已于 `b3e2fb8` 合并；当前 main 为 `e488194`，main push 的免费 smoke/scan 已验证，无付费 Probe。
- #66：当前 SHA 的 PDF→Worker→IndexRun→Active→Search 已真实通过；旧 SIGKILL 的原始 Worker/Beat 日志和内存事件已丢失，历史退出根因仍未验证。#66 单独 Worker 调用缺少 #52 预算代理，因此不得单独合并；已在 #66+#52 临时组合树证明 Qwen Worker 调用和 Search 使用者/租户预算结算。下一步完成 #52 价格快照治理/质量评测，再更新两 PR 基线并做最终合并验收。
- #69：真实 DB Registry→付费 Doubao→隔离 Next BFF/APISIX/FastAPI chat 已成功；新 HEAD `bf857fe` 的免费 CI 全部 PASS，Trace 串线修复及 119 项定向回归通过。下一步补验 Memory、RAG/SQL 授权、SSE resume/取消、费用归因和模型失败终态，未完前保持 Draft。
- #52：最新 HEAD `91db7640` 的 Detect/Secret Scan、preflight 与 smoke wrapper PASS，真实 8-case API Evaluation SKIPPED。与 #66 集成的真实 Worker/Search 预算结算通过，但不能替代检索质量评测。DB 模型角色和付费 Provider 配置可用；阻断来自价格 Registry：本地仅有 primary，GitHub 没有 `PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL`，Doubao approved 行币种与官方 CNY 刊例价不符。需完成正式价格治理并配置只读副本后，才能安全运行 8-case；保持 Draft/BLOCKED，不伪造审批。
- #76：代码已合并；098 在目标共享数据库的应用状态交给获授权部署/迁移流程确认，本任务不写共享或生产库。
- #50 已 CLOSED（未合并）；GitHub 关闭评论记录它的不可用动作门禁已由 #72 合并实现覆盖，并给出当前 main 上 40 项前端定向测试证据。#60 已 CLOSED（未合并）；交接材料移至 #75，后由 #78 合并至 `e488194`，未重复合入旧 Draft。

## 用户追加授权与本轮 #66 真实 PDF 验收（2026-10-16）

- 用户授权使用 `C:\Users\wh\Desktop\不同文件测试文档\testset`，使用隔离数据库，并将真实模型 API 总支出控制在人民币 20 元以内；缺少模型价格时可自行从供应商官方价目页查价并补入隔离评测价格快照。
- 对 testset 执行 `D:\Python\python.exe verify_testset.py`：75 PASS、0 FAIL；其中 `14_long_300_500p/progit_en_501p.pdf` 虽通过 manifest 检查，MuPDF 实际报告 XRef 损坏和结构修复，不能据此将其当成完整解析通过样本。
- #66 工作树 `C:\Users\wh\.codex\worktrees\rag-upload-b1-20261014\agent`，分支 `codex/rag-upload-b1-20261014`；提交 `e2575eb` 修复长文档 chunk 上限与损坏 PDF 页覆盖；`560f1eb` 修复递归分块剥掉边界空格；本轮提交 `ea6eab9` 修复 Parent/Leaf 误去重和有效短 Leaf 被过滤。均已推送，PR 仍为 Draft。新 HEAD Detect/Secret Scan PASS，RAG Smoke preflight 尚 pending，真实 API Evaluation SKIPPED。
- 实测 `progit_zh.pdf`：501 页、500 页有正文、`is_repaired=false`、5,395 chunks、未截断。抽样门禁后又以完整上限复核 AST 的 4,730 个 leaf：4,730/4,730、coverage 1.0、0 uncovered、QualityGate 无硬异常。峰值进程内存约 1.06 GiB。实测 `progit_en_501p.pdf`：`is_repaired=true`、仅 132/501 页有正文、909 chunks，QualityGate 返回硬异常 `pdf_page_coverage`，不会假报 Active；损坏源文件未修改。
- 用户 testset 共 30 个支持格式文件完成一次无付费本地解析矩阵；其中发现并修复 `source_text.pdf` 的 chunk 边界空格丢失，当前 #66 HEAD 复测该 PDF 为 10/10 页、32 chunks、0 coverage 缺口、0 硬异常。testset 的损坏英文 PDF 被 `pdf_page_coverage` 拦截。扫描件 `scanned_partial_fail.pdf` 的离线 RapidOCR 返回 9/10 页并记录 1 页失败；现行 QualityGate 按项目既有规则以 `ocr_coverage_partial` 阻止不完整文档 Active，不能把 manifest 中“其余页正常索引”解释成允许部分正文冒充完整 Active。
- 现有 cleaner 已只移除带明确标签的页码页脚；短裸数字、法规文号年份、表格比例/库存、产品参数均有回归断言，保持不确定时保留正文。
- 定向验证：隔离 Docker PostgreSQL `agent_pr66_unit_20261016` 上 PDF/chunk/quality-gate 套件 51 passed；本轮 `test_rag_upload_to_index_e2e.py` + `test_rag_upload_celery_mode.py` 36 passed，`test_faq_routing.py` + `test_pr55_coverage_quality_gate.py` 27 passed；source_text 与 501 页中文 PDF 实际门禁复测通过。改动文件 py_compile、diff-check 和定向 Ruff 通过（跳过仓库存量 F811 重定义）；secret scan 由新 SHA CI 验证。
- 官方价格源已核实：[阿里云 Qwen3.7 embedding](https://help.aliyun.com/en/model-studio/text-embedding-synchronous-api) 为 CNY 0.0005/1K input tokens；[火山引擎 Doubao Seed 2.0 mini](https://docs.volcengine.com/docs/LakeAIService/Largemodelbilling?lang=en) 对 `doubao-seed-2-0-mini-260428` 输入≤32K 档为 CNY 0.2/1M input、CNY 2/1M output。现有 approved Doubao 行记 USD 0.2/2.0 per 1M，不能当作官方 CNY 价格的精确结算快照；Qwen Registry 行为 USD，换汇后与官方价近似但不完全相等。
- 用户追加 CNY 20 预算已用于隔离真实 API 验证；不使用生产数据库、不打印或持久化凭据。隔离账本累计记录 33 个成功 Provider calls / 57,692 tokens，无 unknown 价格行；现有快照合计 CNY 0.182506，按官方刊例价重算约 CNY 0.0383713（都不是供应商发票）。其中 #66 单独 E2E 确认 DB 配置的付费模型 Provider 调用，但未走预算预占；#66+#52 临时集成后 Qwen Worker 调用与 Search 均按可信 user/tenant/request 预占并结算。完整 8-case 真实检索质量评测仍 SKIPPED，不计通过。
- 运行期间临时 harness 的路径字面量、空隔离向量/BM25 前缀、幂等键复用和搜索标记不匹配均已定位并修复；最终集成 E2E 上传/索引/Search PASS。下一步在价格治理外部条件解决前，继续 #69 可离线验收与 #66/#52 基线施工；价格审批门禁不绕过。

## 2026-10-16 续工：#69 T2 与价格来源核验

- 用户确认 Runtime 模型应从 DB Registry 解析并调用付费 Provider；本任务此前真实请求已证实 main 角色绑定 doubao-seed-2.0-mini。本地 Not a git repo, skipping 只针对测试素材目录；Git 操作始终在登记的任务 Worktree 内完成。
- #69 首次运行 SSE 定向集误用 localhost:5432 的本机 PostgreSQL 18，原始错误为 psycopg2.OperationalError: fe_sendauth: no password supplied；FastAPI 导入阶段 feedback.init_db() 触发。判定为测试环境路由配置，不是产品缺陷。固定到隔离 Docker PostgreSQL 127.0.0.1:5434，数据库 pr69_t3_memory / pr69_t3_business 后，同一 SSE/终态/Resume 集通过：69 passed。
- 当前 #69 HEAD 上定向 Conversation-first、续接、产品范围、Router Engine 集：65 passed。Memory 路由、服务端可信身份、Request Context 与跨请求事件隔离集：57 passed。独立真实 PostgreSQL 测试 test_repo_user_isolation_physical 首次因项目配置校验要求 PGPASSWORD 而失败；隔离 PG 容器使用 trust host auth 且只绑定本机，测试进程设置测试专用占位值后，测试实际写入 A 的记忆、验证 B 物理不可见并清理：1 passed。上述均未调用付费 Provider。
- 官方价格候选（仅拟用于隔离评测快照，不改写历史价格或共享 Registry）：
  - Doubao doubao-seed-2.0-mini，火山方舟常规在线推理、≤32K：input CNY 0.2/M、output CNY 2/M、cached input CNY 0.04/M；[火山方舟官方模型价格](https://console.volcengine.com/ark/region:cn-beijing/docs/model-pricing)，页面更新时间 2026-10-15。
  - Qwen qwen3.7-text-embedding：CNY 0.0005/1K input tokens，即 CNY 0.5/M；[阿里云官方 Embedding 价格](https://help.aliyun.com/en/model-studio/embedding)。
  - Qwen qwen3.7-text-rerank：CNY 0.5/M input tokens；[阿里云官方 rerank 价格](https://help.aliyun.com/en/model-studio/qwen3-7-text-rerank)。
- 当前隔离库已有价格行把 Doubao 人民币单价标为 USD，Qwen 行的币种/金额也不能替代官方 CNY 价格。没有修改这些 append-only 历史行。GitHub prompt-eval 环境未配置 PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL；#52 当前 8-case 真实 API job 是 SKIPPED。项目价格治理还要求真实独立双审与 24 小时 canary；官方报价核验和用户的 API 预算授权不等同于伪造这两项状态。因此尚未写入已批准快照，未进行新的付费调用；待提供/配置经治理的只读 Registry 副本和双审 canary 后再运行当前 HEAD 的 8-case 评测。

## 2026-10-16 续工：DB Provider 配置澄清与当前 HEAD 验证

- 用户澄清：角色从 DB Registry 读取，目标是付费模型 API；“本地不存在 Doubao”不是 Provider 不可用。只读核验运行服务 `agent_memory`：`main/eval_gen/fallback/context_compactor` 绑定 `doubao-seed-2.0-mini`，Provider endpoint host 为 `ark.cn-beijing.volces.com`；`embedding`/`rerank` 绑定 Qwen 专用 API。仅核验配置标识、endpoint host 与凭据记录存在性，未读取、打印或复制任何密钥。该结果确认真实运行配置与本地 Ollama 默认项不同。
- GitHub `prompt-eval` Environment 已有 Doubao、Embedding、Rerank API secret 及 `doubao-seed-2.0-mini`、`qwen3.7-text-embedding`、`qwen3.7-text-rerank` 变量；没有 `PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL`。因此可用模型凭据不是阻断，真实评测仍必须先通过权威只读价格 Registry 的 approved CNY 快照与双审/24h canary。
- 另一个当前隔离库 `agent_rag_e2e_memory` 的角色表此刻仍指向 `qwen2.5:3b`/Ollama 且 Provider credential 行缺失，不能拿它冒充真实付费 Registry 配置。账本复核更正了较早快照：历史上有 7 次 Doubao、29 次 Qwen embedding 调用（含 1 条零 token 的 unpriced 记录）；付费调用合计 57,720 tokens。按已经核实的官方现行 CNY 刊例价、未计缓存折扣重算约 ¥0.0384；旧账本登记约 ¥0.1825，与官方 CNY 刊例价口径不一致，且不是供应商发票。以上为既有隔离记录，本轮没有新付费调用，累计仍远低于用户 ¥20 上限。
- #52 当前 HEAD 免费/本地定向验证：MultiQuery Proxy/Token Budget/Trace、Specialized Budget、Embedding singleton、LLM governance、Billing、Usage provenance、paid preflight 与 eval identity 契约，共 113 passed。GitHub PR 检查中 Detect/Secret Scan 通过，preflight 与 contract-check 当时 pending；8-case 真实评测按 PR 信任边界 skipping，不计通过。
- #69 在原分支新增 `test_repo_tenant_isolation_physical`，隔离 PostgreSQL 同用户跨 Tenant 的查询 A 可见、B 不可见并清理成功；用户隔离与租户隔离合计 2 passed。提交 `8111c4574f1f696dadde03ad442bbd03f078c756` 已推送；该 SHA 的 PR Smoke、preflight、contract-check、Detect、Secret Scan 全部 PASS，真实 API Evaluation 按 PR 信任边界 SKIPPED。测试产生一条 Windows Proactor teardown warning，断言与数据库清理成功。该 warning 不作为通过门禁的替代，也没有改动事件生产代码。

## 2026-10-16 续工：官方价格页复核

- 复核用户提供的三份官方价目页（页面于本轮在线读取）。与上方已记录的现价基准相比，未发现标准调用刊例价变化：火山方舟 Doubao Seed 2.0 mini 常规在线、输入≤32K 仍为输入 ¥0.2/M、输出 ¥2/M、缓存命中输入 ¥0.04/M（页面更新时间 2026-10-15）；阿里云 Qwen3.7-text-embedding 仍为 ¥0.0005/千输入 token（批量 API 另列 ¥0.00025/千）；Qwen3.7-text-rerank 仍为 ¥0.5/M 输入 token。
- 依据：[火山方舟模型价格](https://console.volcengine.com/ark/region:cn-beijing/docs/model-pricing)、[阿里云 Embedding](https://help.aliyun.com/en/model-studio/embedding)、[阿里云 Qwen3.7 Text Rerank](https://help.aliyun.com/en/model-studio/qwen3-7-text-rerank)。这仅确认公开页面与已记录基准一致，不代表隔离/共享数据库内价格快照已治理通过，也不核实账单实扣金额；本轮没有新模型调用。

## 2026-10-16 追加施工：模型价格巡检 Agent 与管理端记录

- 用户要求每天访问三份官方价格页，对比价格变化及系统现行价格；并明确管理端需展示 Agent 状态、操作记录和结果。实现放在独立 Worktree `C:\Users\wh\.codex\worktrees\model-price-monitor\agent`、分支 `codex/model-price-monitor`，基线 `e4881940f1d39f82d1f7a938562e0e611f6677bb`。PR #79 初始提交 `3467621ce8d5319c7d167faffd4669589b331f0c`；增加初始化错误告警收敛后，当前 PR HEAD 为 `1e3f64530dc836acf3534107ca1cee34d4532fff`，base `main`，PR 当前 `OPEN / UNSTABLE`，状态 `IN_PROGRESS`。
- 实现内容：Celery Beat 每日 08:00（Asia/Shanghai）只读抓取火山方舟 Doubao、阿里云 Embedding/Rerank；限制官方 HTTPS 主机与页面体积，解析失败不记零价；比较前次每日快照和当前已审核生效 `model_price`，按状态复用现有告警流程，不调用 LLM、不自动写/审批计费价。迁移 `099_model_price_source_monitor.sql` 在确认 #70 工作树最高迁移为 098 后新增，并登记目标库 memory。
- 运行记录表在任务开始时先插入 `running`，完成后存最终状态、逐来源结果、价格对比和已触发告警事件。管理端模型与供应商页新增“价格巡检”标签；只读端点走可信运营角色校验；页面展示计划时间、来源状态、运行历史、官方/系统价格及差异、价格变更、告警触发状态。因为 webhook 是异步 best-effort，UI 明确不把触发告警等同于送达。
- 在线直连三份官方页面实测取得 5 项价格（Doubao 3 项、Embedding 1 项、Rerank 1 项），最终复验 3/3 来源成功。代码映射曾将短来源别名与持久化 source_id 不一致，现已统一并补回归测试；失败事件现按相同稳定 source_id 写入。
- 隔离 PostgreSQL 18 本机端口 55435，数据目录 `D:\tmp\agent-price-monitor-pg-20261016`，仅应用迁移 099 并使用隔离 fixture `model_price` 行验证运行记录、每日观测幂等、前次官方变化、币种差异和 API 返回序列化。端到端任务记录 5 个来源报价，测试 fixture 产生 1 个 mismatch 告警；该 fixture 仅证明差异识别，不代表共享/生产系统价格结果。PG 集群已停止，未触碰 Docker/shared/production 数据库。
- 最终定向验证：后端 `test_model_price_page_monitor.py` 与 `test_price_api_contract.py` 14 passed；管理端 `modelPrices.test.ts` 与 `ModelPriceMonitorTab.test.tsx` 5 passed；`npx tsc --noEmit`、Python `py_compile` 和 `git diff --check` 均通过。`npm ci --ignore-scripts --no-audit --no-fund` 在隔离 Worktree 成功完成；依赖目录不在 Git 跟踪差异中。没有真实模型 API 调用，无模型费用。
- PR #79 HEAD `1e3f64530dc836acf3534107ca1cee34d4532fff` 的 Secret Scan、frontend-admin-security、frontend-admin-functional、admin-observability-auth、Detect RAG changes、RAG Smoke preflight 和 wrapper 全部 PASS；PR state `OPEN / CLEAN`。RAG Smoke Evaluation（真实 API）为 SKIPPED，不能计为通过或真实模型质量证据。GitHub 自动 Code Review 仍显示运行中，目标 SHA 是初始提交 `3467621`，尚无 Review 结论或 inline comment。
- 下一步：继续等自动 Review 的完成状态，核对是否覆盖初始实现；最终 HEAD 与初始 HEAD 的唯一差异是把 Repository 构造移入既有任务失败捕获范围，并已在新 HEAD 通过后端 14 项定向测试。没有待修复 CI。价格巡检代码尚未合入 main，也没有在部署数据库应用迁移；部署后须运行迁移并确认 Celery Beat/maintenance Worker 已加载此任务，再从管理端观察首条线上运行记录。

## 2026-10-16 续工：PR #79 五条 Review 修复

- 当前功能分支为 `codex/model-price-monitor`，Worktree `C:\Users\wh\.codex\worktrees\model-price-monitor\agent`；已 fetch，`origin/main=e4881940f1d39f82d1f7a938562e0e611f6677bb` 与 PR base 一致。最新提交 `648c78c209ea1a369497972ea3158620caebe882` 已推送到 PR #79；提交仅包含巡检实现、队列映射和对应 API/UI 测试。
- 五条 Review 全部已回复并解决：币种用统一计费 `to_base_currency` 折算到 CNY，按 `model_price` 六位存储精度计算容差并在管理端显示折算值/汇率；禁用自动重定向、下一跳前逐一校验 HTTPS/主机/端口；流式读取且超过 2 MiB 立即中止；所有来源均失败时先保存失败状态和告警再抛出 Celery 异常；改投现有 `report` 队列避开高频 `maintenance` Worker。
- 当前代码定向后端验证：`test_model_price_page_monitor.py`、`test_price_api_contract.py`、`test_task_queue_router.py`、`test_worker_topology.py` 共 48 passed、6 skipped。Python `py_compile`、`git diff --check`、Secret Scan tree（0 命中）通过；管理端 `ModelPriceMonitorTab.test.tsx` 1 passed，`npx tsc --noEmit` 通过。改后抓取器直接访问官方三页实际解析 5 条价格（Doubao 3、Embedding 1、Rerank 1），无抓取失败；没有模型 API 调用。
- PR #79 最新 HEAD 的 PR 检查已完成：Secret Scan、Detect RAG changes、frontend-admin-security、frontend-admin-functional、admin-observability-auth、RAG Smoke preflight、免费 RAG Smoke wrapper 均 PASS；真实 API Evaluation 在不可信 PR 事件下为 SKIPPED。五条 Review Thread 均已回复并确认 `isResolved=true`。PR #79 已正常合并，Merge SHA `333efc650deed0d67868b5f8b2833063b5960daa`，当前 `origin/main` 为该 SHA；main tree 与已验证 PR HEAD tree 一致。
- main push 的 Secret Scan 和 frontend-admin CI PASS；RAG PR Smoke 失败，Run `38894091981`。根因已确定：`.github/scripts/rag_smoke_path_filter.py` 将 `backend/infra/llm/price_page_monitor.py`（命中整个 `backend/infra/llm/` 前缀）和新迁移 `099_model_price_source_monitor.sql`（文件名命中通用 `model` 标记）误判为真实 RAG 变更。Workflow 随后启动隔离评测，但在 `Seed DB API model bindings --require-approved-prices` 因缺少只读 Registry 连接而失败；价格核验、Provider Probe 和 8-case Smoke 均 skipped，没有发起模型调用/产生费用。脚本在本地以 PR #79 完整变更文件列表复现输出 `relevant=true`。
- 下一步在独立 follow-up Worktree 修正 RAG 路径分类：排除价格巡检实现与迁移的精确路径，同时保留真实 LLM/RAG 路径和模型计费迁移触发；新增完整 PR #79 文件集“不相关”和真实 RAG/计费改动“相关”的回归测试。follow-up 通过当前 HEAD CI 后合并，避免之后无关模型价格巡检改动触发付费 RAG 评测。线上价格巡检迁移、Celery 首次运行与 webhook 实际送达仍未验证。

## 2026-10-16 Follow-up #80：RAG Smoke 路径误报

- 在当前 main `333efc650deed0d67868b5f8b2833063b5960daa` 建立独立 Worktree `C:\Users\wh\.codex\worktrees\rag-path-filter-price-monitor\agent`，分支 `codex/rag-smoke-path-filter-price-monitor`；提交 `26af8b6638cfc5ce6e0ff94fb9776d4a32b02102`，PR [#80](https://github.com/Jayus-07/agent/pull/80) 已创建并附加。
- 变更仅涉及 `.github/scripts/rag_smoke_path_filter.py` 与 `tests/test_rag_smoke_path_filter.py`。只排除价格巡检模块与迁移两个精确路径；PR #79 完整变更路径的 push 判定为 `relevant=false`，价格巡检变更与真实 `backend/rag/pipeline.py` 同时出现仍为 `relevant=true`，保留 RAG 评测门禁。
- 本地 `tests/test_rag_smoke_path_filter.py` 与 `tests/evaluation/test_rag_ci_workflow_contracts.py`：12 passed；Python 编译、Secret Scan tree/diff、diff-check 通过。后续最新 SHA 的 Detect、Secret Scan、免费 preflight 和 main-push RAG Smoke Gate 均 PASS；真实 API Evaluation 为 SKIPPED；PR #80 已普通合并为 `e42975d6`，main-push 路径过滤验证价格巡检为 irrelevant。
- 自动 Review 对首版增加的 P1 文档同步意见已修复：更新 `docs/operations/ci-evaluation-environment.md` 触发规则、矩阵和 §8 日期，回复并解决线程。Review 修复提交 `4bf75571b3fc7ed53f1eca290dcf9e9fcd17765d` 已通过最新 HEAD 检查并随 PR 合并。


## 2026-10-16 续工：主助手与 RAG 分支同步 #80 基线

- 本轮用户报出的 `Not a git repo, skipping` 来自测试文件目录检查；测试集目录不是 Git 仓库，这条提示不是 Agent Platform 仓库施工阻断。主仓库根目录仍有用户未提交的三份前端 tsconfig 和未跟踪归档目录，均未改动。
- #69 `codex/main-assistant-conversation-first-20261014` 合入 `origin/main=e42975d` 后 HEAD `89d0e16ed643d88d6e9dc226c093d2e320eb81af`，已推送。指定 14 个会话/事件/身份/API 文件集在隔离 pgvector PostgreSQL（端口 55440，临时测试口令仅进程内）下 `175 passed, 4 warnings`；包括两个物理 PG 用户/租户隔离断言。初次新空库缺表导致 2 个预期集成用例失败，随后只在隔离库用当前 ORM 建表并复跑成功；没有使用共享数据库。最新 PR checks：Detect、Secret Scan PASS；免费 preflight 与 contract-check 排队中；真实 API Evaluation SKIPPED。此前 SHA 的隔离 Next BFF→APISIX→FastAPI 付费 Doubao chat 成功仅覆盖基本 chat，完整 RAG/SQL、取消/resume、Trace/费用 T3 仍未验收，PR 保持 Draft。
- #66 `codex/rag-upload-b1-20261014` 合入同一 main 基线，HEAD `c4faac5ae073c5ff1616ccef31e7b82f22f4bf2f`，已推送。上传/Celery/索引运行时/PDF/coverage/quality gate/失败存储定向集在隔离 PostgreSQL 下 `126 passed, 3 warnings`；首次运行默认连 localhost:5432 导致 1 项失败，改用隔离 DB 后全过。最新 Detect/Secret Scan PASS，preflight pending，真实 API Evaluation SKIPPED。用户 testset 的 PDF 完整链路、主动文档保留与 Search 命中、以及 #66+#52 组合的请求级预算预占/结算均有此前隔离环境证据；该合并基线未改动预算相关实现。#66 仍依赖 #52 的预算门禁，暂不单独合并。
- #52 `codex/rag-multi-query-p0-20261013` 与 `origin/main=e42975d` 合并无冲突，HEAD `4665d4455b63e426fd6c6168712b1c99d4a62362`，已推送。并入 #80 后 `tests/test_rag_smoke_path_filter.py` 仍覆盖价格巡检路径误报和真实 RAG 路径触发；费用、模型绑定、付费前价格校验和工作流定向套件 `128 passed`。首次模型角色 API 子集因未设 AI_INTERNAL_TOKEN 返回 503；进程内临时测试令牌重跑后通过。最新 Detect/Secret Scan PASS，preflight pending，真实 Evaluation SKIPPED。
- 复核 `gh secret list --env prompt-eval` 仅确认测试 Provider API Secret/Base URL 名称存在，不读取 Secret 值；环境缺少 `PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL`。代码要求从现有授权 PostgreSQL recovery 只读副本读取有官方来源、版本、有效期和两位不同审核人的价格行。用户允许使用 ≤¥20 Provider 预算、也允许按官方价格补缺，但这不构成伪造双审、canary 或共用库连接的授权；当前不能安全运行真实 8-case 质量/费用评测。需要外部配置合法只读 Registry 副本，并完成双人审查及 24h canary 后才可继续。
- 最新 main `e42975d6a2992f22bb4a95b9daf806dc3e67d2bf` 已含 #79/#80。#79 main-push 最初误触发 RAG Smoke 的路径分类错误已由 #80 修复；main push run `38896792159` Gate PASS，RAG 不相关路径下真实 API job skipped，无 Provider 调用。
- 下一步：三个 PR 新 HEAD 免费 preflight/contract/Detect/Secret Scan 已完成且通过。恢复授权 PostgreSQL recovery 只读 Registry 连接，在未改写历史价格行的前提下完成可追溯官方价格的双人审批及 24h canary；随后以 ≤¥20 授权预算运行当前 #52 8-case 和 #69 所需真实端到端评测，核实费用账本与原 Top-1 ≥0.80 门槛。之后重跑 #66+#52 当前 HEAD 预算 Worker/Search 集成回归，收口并合并。另由授权发布环境应用 097/098/099 后验证 main 精确 SHA 栈、Celery Beat 首跑和管理端运行历史。


## 2026-10-16 续工：最新 HEAD 回归与 GitHub 门禁

- #69 当前 PR HEAD `89d0e16ed643d88d6e9dc226c093d2e320eb81af`（main `e42975d` 已合入）。后端首轮会话/身份/SSE/路由集 `175 passed`；追加授权 SQL、RAG 链、身份与错误协议集 `216 passed`；主前端 `50 passed`、客服前端 `30 passed`，两端 `npx tsc --noEmit` 均 PASS。GitHub runs `38899600495` 与 `38899600500` 的 Detect、Secret Scan、免费 RAG preflight、contract-check 和 Smoke wrapper 均 PASS；付费 8-case Evaluation 为 SKIPPED，不能算真实质量通过。PR 无未解决 Review thread，仍 Draft。
- #66 当前 PR HEAD `c4faac5ae073c5ff1616ccef31e7b82f22f4bf2f`。定向上传/Worker/PDF/coverage/quality-gate/storage 回归 `126 passed, 3 warnings`；GitHub run `38899683164` Detect、Secret Scan、免费 preflight、RAG Smoke gate 均 PASS；付费 Evaluation SKIPPED。PR 无未解决 Review thread，仍 Draft。首次执行中默认 localhost PG 导致 `DocumentRegistry` 一个集成用例失败；指向隔离 PostgreSQL 后重跑全过。
- #52 当前 PR HEAD `4665d4455b63e426fd6c6168712b1c99d4a62362`。费用/模型绑定/Embedding/MultiQuery/路径过滤集 `128 passed`；首次 API 子集没提供 AI_INTERNAL_TOKEN 返回设计要求的 503，进程内测试值重跑全过。GitHub runs `38899713137`、`38899713909` 的 Detect、Secret Scan、免费 preflight、RAG Smoke gate 均 PASS；真实 Evaluation SKIPPED。14 条原 Review thread 全解决，PR 仍 Draft。
- 加跑 `test_runner_trace_finalize.py` 时 `test_generator_close_finishes_once_as_client_disconnect` 首帧为 `log`，不符合该断连用例对首帧 `status/delta` 的附加断言；单测在 #69 HEAD 与独立干净 main `e42975d` 上均复现同样失败。生产 runner 与该断言在 main 已有，#69 只为 FakeMemory 新增 `event_scope` 参数适配；因此记录为历史基线测试预期错误，不将其归因于 PR，也不为获得全绿而放宽断言。
- `gh secret list --env prompt-eval` 只列出了测试 Provider Secret/Base URL 的名称，未读取任何值；仓库及该 Environment 均无只读价格 Registry Secret。真实评测程序要求 PostgreSQL recovery 只读副本，并验证官方来源、价格版本/有效期及两位不同 reviewer；`llm-budget-billing.md` 另要求 24 小时 canary。用户允许补充官方价格和总额 ≤¥20 的 Provider 调用，但不授权伪造双审/Canary 或绕过共享 Registry 边界。当前没有新的付费调用。
- 本轮临时建的 `D:\tmp\agent-pr69-final-baseline-20261016` 已核对干净后移除；本轮创建的 `pr69-isolated-pg-20261016` 与 `pr69-isolated-pgvector-20261016` 已连同匿名卷移除。#66 的 `pr66-worker-e2e-20261016-pg` 等现有容器未触碰。
- 当前 `origin/main` 仍为 `e42975d6a2992f22bb4a95b9daf806dc3e67d2bf`。已合并功能的 #79/#80 局部门禁通过；其余可继续施工项为 #69 尚未完成的真实授权 RAG/SQL/取消与全链路 Trace/费用，以及 #66+#52 当前 HEAD 组合 Worker 预算回归。真实 8-case 质量门禁具体阻断是缺少只读价格 Registry 与双审/24h canary；不把 skipped 评测记为 PASS。


## 当前 main 的巡检功能集成状态

| 项目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| main SHA 与合并 | PASS | `origin/main=e42975d6a2992f22bb4a95b9daf806dc3e67d2bf`，含 #79（`333efc6`）与 #80（当前 Merge SHA）。 |
| 价格巡检管理端界面与 API | PASS（代码/CI） | #79 管理端组件、API、后端权限与监控运行记录测试通过；#79 PR 前端功能与安全检查 PASS；#80 修复价格巡检路径被误触发付费 RAG 测试的问题。 |
| Celery 每日采集与数据库迁移 | NOT VERIFIED（部署） | PR 内官方三页抓取 3/3 成功、共解析 5 项；迁移 `099_model_price_source_monitor.sql` 已随 #79 合入，但未在部署数据库应用，未观察线上 Beat 首跑或 Alert Webhook 实际送达。 |
| 发布回滚 | READY | 若回滚巡检功能，按主线正常 revert #80 后 revert #79 两个 Merge SHA；部署回滚前同时按发布流程回滚 migration 099/应用版本。不得直接删除已应用迁移。 |
| 上线就绪 | BLOCKED | 管理端只在代码部署且 migration 099 已由授权发布流程应用后可查看运行记录；整体 MAIN-01～34、#69 主助手真实 T3、#52 真实质量/账本未完成。 |

## 2026-10-16 Phase2 续工：#77 P0-A 本地修复

- 修复分支 `codex/pr77-price-isolation-20261016` 从最新 `origin/main=e42975d6a2992f22bb4a95b9daf806dc3e67d2bf` 创建，工作树 `C:\Users\wh\.codex\worktrees\pr77-price-isolation-20261016\agent`。未修改主工作区或其他 PR 工作树。
- A1：seed 使用显式测试库 URL/会话；仅允许本机 loopback 主机和 `_test`/`_eval` 后缀库名；校验库名、角色、host、port、read-only 与 recovery 身份后才执行首个写入；`MEMORY_PGDATABASE` 冲突、远端 host、非测试库或缺少连接角色均在建写连接前拒绝。`verify_pricing` 通过只读测试库连接工厂检查统一 BillingResult。
- A2：Registry、在线价格仓储和官方价格巡检均关联 `model_price_versions`，限定 active 且来源一致；快照保留原版本发布证据，拒绝缺失维度、混版本/来源/币种；在线 `PriceTable` 也拒绝同一模型组件混版本或币种。
- A3：路径过滤改用精确相关迁移清单并加入 `013_obs_analytics_pg.sql`；未知 migration PR 运行免费契约检查但不自动触发付费评测；客服、旅游、只读价格巡检路径不触发付费 RAG 评测。
- 验证：定向价格/seed/计费/路径套件 `84 passed`；PostgreSQL active 价格选择集 `2 passed`；py_compile、Ruff（忽略仓库既有 N818）和 `git diff --check` 通过。隔离容器 `codex-pr77-price-isolation-20261016` 上真实 seed 写入目标 `agent_memory_test`、角色 `postgres`，3 条凭据均为密文；无 Provider 调用。此前目标身份校验失败在任何写入前退出，冲突/远端/非测试目标的单测确认不会创建写连接。
- 当前 P0-A 代码尚未提交、尚未创建 PR，GitHub Review/CI 未开始。下一步：完成本地 Diff 最终检查后提交、推送并创建 Follow-up PR；收口最新 HEAD Review/CI 后再进入 #79。

## 2026-10-16 Phase2 P0-A 收口：#81

- 提交 `f1ca05cb95d13aec9bd837fab3bffb703bc81a6f` 推送并创建 Follow-up PR [#81](https://github.com/Jayus-07/agent/pull/81)。自查 Review 覆盖 seed/Registry 写入边界、active 价格读取、费用币种/版本一致性与 CI 分类；PR Review Threads 为空。
- GitHub `secret-scan` PASS，PR RAG Smoke 免费 preflight/契约测试 PASS；真实 8-case API 评测按不可信 PR 事件 SKIPPED，不计质量通过。PR #81 已按授权普通合并，Merge SHA `89315c9d32b383d31dd390d7a90dbe6b9c4eb75b`。
- 合并后 `origin/main` 为该 Merge SHA，main tree `bd4c13738868e3d45e393fcca1a242e7e06efcac` 与已验证 PR tree 完全相同；同一隔离 PostgreSQL 环境重跑受影响定向集 `84 passed`。
- P0-A1～A3 代码修复与验证收口。下一步按阶段顺序新建独立 #79 Follow-up 工作树处理 B1～B3；P2 真实价格 Registry/隔离评测边界留到 P1 后继续。

## 2026-10-17 总控文档修复与 Phase2 证据归并

- 事实：PR #82 分支把本目录的 `TASK.md`/`PROGRESS.md` 整体替换成 28 行的「PR-INTEGRATION-PHASE2-20261016」版本，随 `0bc9038` 进入 main，导致原 329 行总控记录在 main 上丢失。本轮按 Phase2 版本内容恢复完整总控文档（本文件即续写），并把其中有效证据并入下方章节，不另建总控文档。
- Phase2 版本记录的有效证据：#81 合并 `89315c9d`（P0-A 价格治理/评测隔离/CI 路径过滤，定向 84 passed，隔离 PG，无 Provider 调用）；#83 合并 `e354f3b3`（#73 模型健康，后端 43 + 前端 60）；#82 首轮 CI 因管理端 settings/models 摘要漏 `cancelled` 状态失败并修复，最终 `1d548bd` 定向 4 passed（隔离 PG 告警恢复/revoke）+ 6 passed（价格档位/任务中断）+ 3 passed（管理端），TypeScript/Python/diff 检查通过。
- 继承约束：真实评测累计费用 ≤ ¥20；无法证明费用上界时不得调用 Provider；迁移只在隔离库执行。

## 2026-10-17 只读核对：#82 工作树与 main 的 tree 一致性

- `C:\Users\wh\.codex\worktrees\price-monitor-followup\agent`：工作树干净，HEAD `53edac1` 是 `origin/main=0bc9038` 的祖先（`git merge-base --is-ancestor` 成立）。
- `git diff HEAD origin/main` 只列出 #83 的 13 个模型健康文件；`git diff HEAD...origin/main` 为空。
- 结论：此前观察到的 tree SHA 不一致（`9d0c2fd` vs `dafdaf0`）来自 #83 先于 #82 合并的顺序，不是工作树被覆盖或丢失内容；未做覆盖或强制同步，工作树可继续使用。

## 2026-10-17 #84（#74 旅游 Follow-up）收口

- 起点：HEAD `add3fad`、base `e354f3b`，GitHub `mergeStateStatus=DIRTY / mergeable=CONFLICTING`。`add3fad` 的 CI：Detect、Secret Scan、免费 preflight、RAG Smoke gate PASS；8-case 真实 API 评测 SKIPPED（不计通过）。
- 该 HEAD 的自动 Review（2026-10-16T19:24:07Z）新增 1 条未解决 P1：把 `sk` 纳入标签式凭据脱敏（`sk abc…`、`sk=abc…`、`sk_abc…`），同时保留标准 `sk-` 匹配。
- 冲突根因：main 新增 `backend/sql/migrations/100_model_price_change_alert_outbox.sql`，与本 PR 的 `100_travel_v2_trip_conversations.sql` 重号；`scripts/init_db.py` 因两侧在同一字典相邻行追加而真实冲突。
- 处置（提交 `a5c8174`）：合并 `origin/main=0bc9038`（merge commit `42dfcbe`，冲突文件只有 `scripts/init_db.py`，按 main 版本 + 保留 travel 登记解决）；travel 迁移重号为 `101_travel_v2_trip_conversations.sql`（含 `init_db.py` 两处、迁移登记测试与模板复制测试的路径）；脱敏正则把 `sk` 纳入标签分支并允许 `_` 作分隔符（保留 `sk-` 独立规则）；补 4 条 `sk` 分隔符回归断言。
- 真实回归发现并修复（提交 `29ae19d`）：在隔离库跑全 `backend/tests/travel_v2` 时 `test_postgres_catalog` 仍按旧表集合断言，未包含本 PR 新增的 `trip_conversations`；CI 因 PostgreSQL 依赖 skip，此前未暴露。已补表名与 `uq/fk/chk_travel_v2_trip_conversation*` 三个约束断言。
- 实际验证（隔离容器 `travel84-isolated-pg`，仅 `127.0.0.1` 动态端口，未连共享库）：`init_db.py` 全链 rc=0 且 `101_travel_v2_trip_conversations.sql` 正常应用；`backend/tests/travel_v2` **176 passed**；前端 5 个 travel 测试文件 **62 passed**；`npx tsc --noEmit` PASS；Python AST 解析与 `git diff --check` PASS。
- 未执行：真实 Provider/8-case 评测仍 SKIPPED；本轮 0 次模型调用、0 费用。
- 合并门禁：`29ae19d` 的 Detect、Secret Scan、免费 preflight、RAG Smoke gate 全部 SUCCESS，8-case 真实评测 SKIPPED（不计通过）；Review 线程 0 未解决（`add3fad` 的 P1 已回复证据并 resolve）。自动 Review 对 `29ae19d` 在等待约 25 分钟并显式 `@codex review` 后仍未返回，按“必需检查全绿 + 0 未解决线程 + CLEAN”合并，未假设其结论为通过。
- 合并结果：PR #84 普通 merge，Merge SHA `ad553d00cd63d18bbf9bef20506989c89fd8dff5`（2026-10-17T08:27:10Z）；`origin/main` 现为该 SHA，main tree `c894bac02923bbcfe4854e971507b75d058c77a9` 与 PR HEAD tree 完全相同。
- 合并后验收：main-push run `39017414538` 的 Detect 判定 `relevant=false`，付费 preflight 与 8-case 均 skipped，gate PASS（无 Provider 调用、无费用）；`39017414552` secret-scan PASS。在精确 Merge SHA `ad553d0` 的独立 detached 工作树重跑 `backend/tests/travel_v2`：**176 passed**。

## 2026-10-17 P2 只读核查：正式价格审批、evaluation-only 与累计预算

1. 正式审批链（`backend/infra/llm/price_governance.py`）：`import_version` 写入 `approval_status='pending'` → `review_version` 要求两名不同审核人且导入人不得审核 → `canary_version(start)` → 满 24 小时 → `complete` 要求覆盖率 100%、缺价与价格计算错误为 0，才生成 `approved` 行并把旧版本置 `expired`。当前没有任何已批准的 CNY 版本，GitHub `prompt-eval` 环境仍缺 `PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL`。
2. 代码缺口 1：`import_version` 的 INSERT 把 `currency` 写死为 `'USD'`，经治理链导入的人民币官方刊例价必然被记成 USD——这是 Doubao 人民币价被记成 USD 的机制性原因，不是数据录入失误。修复需独立 PR（含兼容既有行与测试），本轮仅记录。
3. 代码缺口 2：`backend/evaluation/evaluator_cost.py` 用静态 `get_model_pricing` 估算评测器成本，不读已批准 `model_price` 的版本/币种/来源，评测计费与正式账本口径可能不一致。
4. 累计预算：`backend/infra/llm/budget.py` 只有请求级上限（calls/tokens/cost/retries/fallbacks）；`backend/infra/llm/quota.py` 有租户/用户日/月配额（隔离库策略行：platform default 144/2160 CNY、tenant test 72/720 CNY）；`backend/evaluation/run_guards.py` 每 10 个 case 查一次预算并熔断，但**查询失败放行**。三者都不是「本轮授权 ¥20」的运行级金额硬上限，默认配额远高于 ¥20，因此无法用现有机制证明 8-case 评测的费用上界 ≤ ¥20。
5. 结论：在 2/3/4 解决且存在已批准 CNY 价格版本之前，真实 8-case、#52 质量评测与 #69 付费 T3 仍为 BLOCKED；官方页面价格不能替代审批行，也不得据此宣称 MODEL_QUALITY_PASS。

## 2026-10-17 P3 基线同步（#66 / #69）

- #66 `codex/rag-upload-b1-20261014`：与 `origin/main=ad553d0` 干净合并（无冲突），新 HEAD `c8663b12771b91b34299f7541cbd48d95f72bd1e` 已推送。定向回归（`test_rag_upload_celery_mode`、`test_rag_upload_to_index_e2e`、`test_index_task_runtime`、`test_upload_failures_store`、`test_faq_routing`、`test_pr55_coverage_quality_gate`）在隔离 PostgreSQL `agent_memory`：**83 passed, 3 warnings**（Windows Proactor teardown 既有告警）。PR 仍为 Draft：Worker 预算仍需 #52，不能单独合并。
- #69 `codex/main-assistant-conversation-first-20261014`：与 `origin/main=ad553d0` 干净合并，新 HEAD `f80d082ceb94ef622eb7ba56047f43febeea9f49` 已推送；工作树 `C:\Users\wh\.codex\worktrees\pr69-main-sync\agent`（原分支此前无工作树）。事件作用域/SSE 契约定向回归（`test_stop_c_event_isolation`、`test_sse_event_schema`、`test_stream_events_flow`、`test_runner_terminal_semantics`）：**88 passed**。`test_runner_trace_finalize.py::test_generator_close_finishes_once_as_client_disconnect` 是 main 基线既有失败，本轮未纳入该集合。PR 仍为 Draft：真实授权 RAG/SQL、取消/resume、Trace 与费用 T3 未完成。
- 两个新 HEAD 的免费 CI（run `39017731249` / `39017670067`）：Detect、Secret Scan、免费 preflight、RAG Smoke gate 全部 success；8-case 真实 API 评测仍 SKIPPED（不计通过，无 Provider 调用）。PR 均仍为 Draft，等真实 T3 与 #52 依赖解决后再按门禁合并。
- #52 `4665d445`：与 `ad553d0` 仍冲突，冲突文件为 `backend/infra/llm/pricing.py`、`backend/rag/retrieval/multi_query.py`、`backend/tests/evaluation/test_rag_paid_preflight.py`、`backend/tests/scripts/test_seed_prompt_eval_db.py`。冲突集中在价格治理实现与对应测试，需在 P2 缺陷 1/2 的处理口径确定后再解，避免用合并手法掩盖价格口径分歧；本轮未强行解决。

## 下一步（按依赖顺序，2026-10-17 更新）

1. 已完成：#84 合并 `ad553d0`，main 定向复跑 176 passed。
2. P2：`import_version` 币种写死（缺陷 1）与评测成本口径（缺陷 2）需独立 Follow-up PR 修复并配测试；运行级 ¥20 硬上限与 `PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL` 只读副本仍待外部条件。
3. P3：#52/#66/#69 仍是 Draft 且与 `ad553d0` 冲突（#52 `CONFLICTING`、#66/#69 待重算）；先各自合入最新 main 并复跑定向集，再按 #66 → #52 顺序集成，#69 补真实授权 RAG/SQL/取消/Trace/费用 T3。
4. P4：097/098/099 仅在隔离库验证；目标库应用状态由授权发布流程确认（本轮隔离库已验证 097/098/099/100/101 全链 rc=0）。
5. P5：最终以实际最新 main SHA 与真实验证证据收口 MAIN-01～MAIN-34。

## 2026-10-17 Phase3（PR-INTEGRATION-PHASE3）执行记录

基线核对：`origin/main=ad553d00cd63d18bbf9bef20506989c89fd8dff5`；#52 CONFLICTING(Draft)、#66 `c8663b1`(MERGEABLE/Draft)、#69 `f80d082`(MERGEABLE/Draft)、#81/#83/#82/#84 已合并。未改写任何已合并提交。

### Phase A｜#81/#83 遗留补修（已提交 PR，待 Review/CI 收口）

**A1 在线计费价格不可见（已复现并修复）**

- 复现：`PostgresPriceRepository.get_current()` 用 INNER JOIN 要求 `model_price_versions.status='active'`，而 `services/model_config._apply_model_pricing` 写入的 `source='provider-page'` 行**不写版本行** → 直登价在在线计费与价格巡检中完全不可见（退化成 registry_fallback）。除价格数据外无其他解释路径。
- 修复：新增共享谓词 `pricing.current_price_eligibility_sql()`（`pricing.py` 与 `price_page_monitor.py` 共用）：治理 `active` 版本入选；无版本行的 `provider-page` 直登价在**不存在 active 治理版本**时入选（治理版本优先）；`expired`/`rejected` 永不入选。
- 冲突失败关闭：新增 `PriceConflict`；跨版本/混币种时 `get_current` 报冲突，`price_usage` 记 `price_unknown`/`unpriced` 且**不**退注册表估价。
- 硬预算缺价处置（`budget.py`，取代 2026-09-22「缺价一律放行」）：冲突→拒绝；无有效价格行且注册表无正单价→拒绝（`price_unavailable`）；注册表仍有正单价→放行 + `price_unknown`。
- 文档：`docs/governance/llm-budget-billing.md` 新增 §4.2（两条写入路径信任级别、有效价格判定、缺价处置）。

**A2 评测库 Docker 端口误判**：`seed_prompt_eval_db._assert_database_identity` 不再比较容器内监听端口与宿主目标端口；身份仍由库名/角色/read-only/主库状态/真实服务地址判定（`_server_address_matches_target` 保留 Docker 私网地址兼容）。宿主 5433→容器 5432 通过，非测试目标继续拒绝。

**A3 未分类迁移漏检**：`rag_smoke_path_filter` 新增 `NON_AI_MIGRATION_ENTRIES`（63 条显式清单）、`classify_migration()`、`unknown_migrations()`。未分类迁移：PR 免费预检**退出码 2 + 可操作报错**（不再静默通过），main push 判为 relevant（交给受信任门禁，付费步骤自身价格门禁仍失败关闭）。

**A4 OCR 缓存版本错用**：`_cache_path()` 改用 `upstream_model`（实际出站模型）而非注册显示名；缓存记 `model`+`upstream_model`；结果元数据新增 `upstream_model`。API Key 不进入键/文件/日志。

实际验证（隔离 PostgreSQL `price-a1-isolated-pg` 127.0.0.1:55511，仅 loopback；PG16/pgvector）：

- 新增 `backend/tests/test_llm_pricing_provider_page_pg.py`（真实 PG，7 项）：修复前 4 项 RED（直登价不可见、跨版本冲突、混币种冲突、硬门缺价放行），修复后 **7 passed**。
- 价格/计费回归：`test_llm_pricing_pg`、`test_pricing_snapshot`、`test_llm_budget`、`test_model_price_page_monitor`、`test_billing_contract`、`test_usage_billing`、`test_model_config_pricing` **86 passed**。
- 相邻门禁：`test_llm_bind_tools`、`test_stream_usage`、`test_eval_model_routing`、`test_rag_paid_preflight` **40 passed**。
- 种子脚本 `test_seed_prompt_eval_db` **15 passed**；路径过滤 `tests/test_rag_smoke_path_filter.py` **12 passed**；OCR `backend/tests/rag/test_pdf_ocr.py` **27 passed, 1 skipped**。
- 本轮 **0 次模型调用 / 0 费用**；未写共享或生产数据库；未执行真实 8-case。

PR：
- [#85](https://github.com/Jayus-07/agent/pull/85) `codex/price-legacy-visibility-20261017`，HEAD `838a3e26d4daa02e591d0e3c1bd28b1bead9decb`，worktree `C:\Users\wh\.codex\worktrees\price-legacy-visibility-20261017\agent`（A1+A2+A3）。
- [#86](https://github.com/Jayus-07/agent/pull/86) `codex/ocr-upstream-cache-20261017`，HEAD `ddd5b63d3285d76059d6b3b584ead2ffb2e139f3`，worktree `C:\Users\wh\.codex\worktrees\ocr-cache-upstream-20261017\agent`（A4）。
- 两者当前 `MERGEABLE`，Detect/Secret Scan SUCCESS，免费 preflight 进行中，8-case SKIPPED（不计通过）。自动 Review 尚未返回。

行为变化需评审确认：硬预算下「无价格行且注册表无正单价」由放行改为拒绝。

### Phase B｜#66 PDF 真实链路（本轮未完成）

- #66 已同步 `ad553d0`，HEAD `c8663b12771b91b34299f7541cbd48d95f72bd1e`；定向回归 83 passed，免费 CI 全 SUCCESS。
- 隔离环境仍可用：`pr66-worker-e2e-20261016-pg`（127.0.0.1:5434，库 `agent_rag_e2e_memory`/`agent_rag_e2e_business`）、`pr66-worker-e2e-20261016-redis`（127.0.0.1:6382）。
- 已确认当前 HEAD 的 Celery app 可加载，`tasks.execute_index`/`tasks.reindex_document` 路由到 `rag_index` 队列；broker 通过 `CELERY_BROKER_URL` 配置（默认 `redis://localhost:6379/1`，本轮未改默认值）。
- 未完成：真实 Worker 启动 + PDF upload → IndexRun 终态 → Active → Search 未执行；SIGKILL 退出码/信号、cgroup 峰值、concurrency/prefetch、table_describe 内存仍未取证。**PDF 链路保持 NOT VERIFIED / BLOCKED**，不得据「代码已同步、解析测试通过」宣称 E2E 通过。
- 续工入口：在 `C:\Users\wh\.codex\worktrees\rag-upload-b1-20261014\agent` 以 `CELERY_BROKER_URL=redis://127.0.0.1:6382/0`、`PGPORT=5434`、`MEMORY_PGDATABASE=agent_rag_e2e_memory`、`BUSINESS_PGDATABASE=agent_rag_e2e_business` 启动 `celery -A backend.tasks.celery_app worker -Q rag_index -c 1 --prefetch-multiplier 1`，用 testset PDF 走上传 API，记录 task_id/run_id 与终态并采样 RSS。

### Phase C/D 状态

- #52：仍 `CONFLICTING`，冲突文件 `backend/infra/llm/pricing.py`、`backend/rag/retrieval/multi_query.py`、`backend/tests/evaluation/test_rag_paid_preflight.py`、`backend/tests/scripts/test_seed_prompt_eval_db.py`。**本轮 A1/A2 恰好改动了其中三个**，合并 #85 后冲突面会显著缩小；建议 #85 合并后再同步 #52。
- #69：`MERGEABLE`，事件作用域 88 passed；真实 T3 未执行。
- 真实 8-case / #52 质量评测：仍缺已批准 CNY 价格版本与 `PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL`；未发起付费调用。

### 下一项施工动作（按序）

1. 收口 #85/#86 的 CI 与自动 Review，按门禁合并并记录 Merge SHA（注意：#86 改动 `backend/rag/**`，合并到 main 会触发 RAG Smoke；无已批准价格时会失败关闭，不产生 Provider 费用）。
2. #85 合并后同步 #52，按新价格可见性契约重解冲突，再跑其费用/绑定定向集。
3. Phase B：按上面的续工入口完成 PDF 真实链路与退出证据。
4. P2 剩余：`import_version` 写死 USD、`evaluator_cost` 用静态价格（本轮未改，需独立 PR）。

## 2026-10-17 Phase3 续工：#85/#86 合并、#52 同步

### 合并结果

| PR | HEAD | Merge SHA | 说明 |
| --- | --- | --- | --- |
| #85 价格治理遗留 | `838a3e26` | `6c7208e233b385674db51d41c181236d8d9f7d54` | A1 有效价格可见性 + 冲突失败关闭 + 硬预算缺价拒绝；A2 端口映射；A3 迁移分类完整性 |
| #86 OCR 缓存 | `d49b099a90b0c0e3b982c5feda6beea3632ba8e3` | `50a4a16ad0470e17e0a60cf8aee99f090039a4c7` | A4 缓存键用 upstream_model；含评审 P2 修复（缓存命中恢复归因） |

- #86 的自动 Review 给出 1 条 P2（全文档命中缓存时 `upstream_model` 归因为 None）。已修复：命中分支从缓存字段恢复、旧缓存回退 runtime；MISS 路径也显式归因。回复并 resolve 线程，复核 `test_pdf_ocr.py` **29 passed, 1 skipped** 后合并。
- 合并前 #85/#86 均 `MERGEABLE/CLEAN`，Detect、Secret Scan、免费 preflight、RAG Smoke gate SUCCESS，8-case 为 SKIPPED。

### main push 的费用证据（新增硬事实）

- `6c7208e` 的 main-push `RAG PR Smoke`（run `39020408434`）判定 `relevant=true`，付费 job 进入 "Seed DB API model bindings" 后**失败关闭**：`RuntimeError: 未配置只读价格 Registry 连接；真实评测保持阻断`（`PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL` 为空）。**未执行任何 Provider Probe，0 费用**。
- 副作用（需外部动作）：由于 #85 改动了 `backend/infra/llm/pricing.py`，#86 改动 `backend/rag/**`，此后相关 main push 会走受信任门禁并因缺少只读 Registry secret 而**红**。这是价格门禁按设计失败关闭，不是代码回归；恢复绿灯只能通过配置 `PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL`（已批准价格只读副本）完成，不能用跳过门禁的方式变绿。

### #52 同步与冲突重解

- 新 HEAD `4938e68f1841d88052e0276c836f2a58780531ab`（合入 `6c7208e`），GitHub 已从 CONFLICTING 变为 **MERGEABLE**（仍 Draft）。
- 冲突 4 个文件，全部按语义合并，未整文件 ours/theirs 覆盖：
  - `backend/infra/llm/pricing.py`（`PriceTable.__init__`）：保留 #52 的 per_call/per_1m_tokens 单位校验与"同维度首行有效"，同时保留 #81/#85 的币种**与版本**一致性校验。
  - `backend/rag/retrieval/multi_query.py`：采用 main 版本（价格档位门禁 `enforce_model_input_price_tier` + `max_tokens=_rewrite_max_tokens()`），保留 #52 的 `_resolve_rewrite_llm` 入口。
  - `backend/tests/evaluation/test_rag_paid_preflight.py`：保留 #52 的 4 条额外用例与 `Path/SimpleNamespace` 导入，并补回 `usage_pricing_called` 断言（价格核验先于 usage 计价）。
  - `backend/tests/scripts/test_seed_prompt_eval_db.py`：保留 #52 的 `multiquery` 参数化绑定断言 + main 的配置缓存/身份校验用例。
- 合并树定向回归（隔离 PostgreSQL 55511 `agent_memory`）：`test_rag_paid_preflight`、`test_seed_prompt_eval_db`、`test_multi_query_token_budget/trace/context/rhetorical`、`test_llm_pricing_pg`、`test_pricing_snapshot`、`test_llm_budget`、`test_billing_contract`、`test_llm_bind_tools`、`test_rag_smoke_path_filter` 共 **150 passed**；`git diff --check` 干净。
- 合并后按新契约修正了 3 处测试期望（unpriced 结算需注册表单价放行、混币种报错文案、价格核验早于计价的断言），均为契约对齐而非放宽断言。
- 仍未完成：#52 仍 Draft；真实 8-case 质量评测（Top-1 ≥ 0.80）与费用账本对账需已批准 CNY 价格版本与只读 Registry，未执行。

### Phase B（#66 PDF 真实链路）：本轮未完成

- 前置已完成：#66 分支已同步 `ad553d0`（HEAD `c8663b1`），定向 83 passed，免费 CI 全绿。
- 已确认：当前 HEAD 的 Celery app 可加载，`tasks.execute_index`/`tasks.reindex_document` 路由到 `rag_index`；隔离栈 `pr66-worker-e2e-20261016-pg`(5434)/`-redis`(6382) 仍在运行，库 `agent_rag_e2e_memory`、`agent_rag_e2e_business` 可用。
- 未完成原因（具体）：真实 PDF 链需要同时满足「Worker 进程 + 解析/分块 + 向量化（本地 BGE 模型加载，内存/耗时峰值）+ IndexRun 终态 + 授权 Search」；本轮剩余执行窗口不足以完成一次可取证的真实运行，且不能把解析单测或 mock Celery 当成 E2E。PDF 链路保持 **NOT VERIFIED / BLOCKED**。
- 续工入口（不变）：在 `C:\Users\wh\.codex\worktrees\rag-upload-b1-20261014\agent` 设置 `PGHOST=127.0.0.1 PGPORT=5434 PGUSER=postgres MEMORY_PGDATABASE=agent_rag_e2e_memory BUSINESS_PGDATABASE=agent_rag_e2e_business CELERY_BROKER_URL=redis://127.0.0.1:6382/0 RAG_DATA_DIR=<隔离目录>`，启动 `celery -A backend.tasks.celery_app worker -Q rag_index -c 1 --prefetch-multiplier 1`，用 testset PDF 走上传路径，采样 Worker RSS 并记录退出码/信号与 IndexRun 终态。

### 下一步（按序）

1. #52：等 `4938e68` 的新 HEAD CI 与自动 Review 收口；真实 8-case 仍需外部价格治理条件。
2. Phase B：按上方入口完成 PDF 真实链路（这是 #66 转 Ready 的唯一前提）。
3. #69 真实 T3（授权 RAG/SQL/取消/Trace/费用）。
4. P2 剩余：`import_version` 写死 USD、`evaluator_cost` 用静态价格（独立 PR）。

### Phase B 追加实测（2026-10-17，未通过）

在 `C:\Users\wh\.codex\worktrees\rag-upload-b1-20261014\agent`（HEAD `c8663b1`）用隔离栈做了真实 Worker 探测：

- 环境：`PGPORT=5434 MEMORY_PGDATABASE=agent_rag_e2e_memory BUSINESS_PGDATABASE=agent_rag_e2e_business CELERY_BROKER_URL=redis://127.0.0.1:6382/0 RAG_DATA_DIR=<隔离目录>`，`celery -A backend.tasks.celery_app worker -Q rag_index -c 1 --prefetch-multiplier 1`。
- 首次启动失败：`MemoryDatabaseUnavailable: PostgreSQL 连接配置缺失: PGPASSWORD`（隔离库是 trust 认证，但 `memory.database._validate_config` 要求非空口令）。补 `PGPASSWORD=trustauth` 后 **Worker 正常就绪**，`[LLMRegistry] DB 覆盖层已注入: 7 个厂商实例 / 8 个自建模型 / 0 份凭据 / 2 个角色覆盖`，`rag_index` 队列被消费。
- 通过 `sync_upload_impl`（与真实上传同一函数）上传 `staff_regulation_scnu.pdf`（542,265 B，ASCII 副本）：返回 `ok=true, upload_id=e5d448d96340`，但 **未产生 `rag_index_runs` 行、`tasks` 行或 `doc_registry` 行**；查询确认 `rag_index_runs` 最近两行仍是此前历史失败（`coverage_gap`、`runtime 模式只读索引不可用`）。
- 直接证据：该 `upload_id` 无 `generation`，`_dispatch_index` 因此不建 run 行；任务即使入队也未形成可追踪终态。这不是「PDF 链路通过」，而是新发现的可追踪性缺口（与历史 `table_describe` 悬挂是同一类问题：任务无 run 行 → 无法证明终态）。
- 结论：PDF 真实链路仍 **NOT VERIFIED**；下一步应先修「上传未带 generation 时不建 IndexRun」的可追踪性（或确保上传路径始终生成 generation），再复跑本探测。Worker/DB/Redis 隔离栈状态正常，无共享库写入，无 Provider 调用。
- 已清理：临时 driver 脚本已删除，Worker 进程已停止；#66 工作树干净（HEAD `c8663b1`）。
