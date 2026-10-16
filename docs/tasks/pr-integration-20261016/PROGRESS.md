# PR-INTEGRATION-20261016 进度

更新时间：2026-10-16（Asia/Shanghai）
任务状态：IN_PROGRESS
主控分支：codex/pr-integration-20261016
主控 Worktree：C:\Users\wh\.codex\worktrees\pr-integration-20261016\agent
当前基线：origin/main = e4881940f1d39f82d1f7a938562e0e611f6677bb；主控任务树已合入该基线

## G0 统一基线

| 门禁 | 状态 | 证据 |
| --- | --- | --- |
| G0-01 main/远端 | PASS（隔离任务树） | 原 main checkout HEAD c5a2703 落后远端且仍有用户修改；隔离控制树已 fetch 并合入 origin/main `e488194`。该 SHA 是 #78 Merge SHA。未在原 main checkout 上 checkout、pull 或写入。 |
| G0-02 PR 状态 | PASS | 已核对 #73/#74/#75/#76/#77/#78 合并状态及 #52/#66/#69 当前 HEAD、Review、checks 和 skipped jobs；#50/#60 已关闭。#77 四条 Review 已解决；#78 合并后 #75 三条旧 Review 均已追加证据并解决。 |
| G0-03 Worktree 归属 | PASS | 控制树、#66、#69、#52 和历史 Follow-up 均为独立 worktree；本轮确认相关 PR worktree 干净后再施工，没有覆盖其他任务修改。其他无关 worktree 均未触碰。 |
| G0-04 避免覆盖 | PASS | 根目录 main checkout 中 frontend-admin/tsconfig.json、frontend-cs/tsconfig.json、frontend/tsconfig.json 的用户修改及未跟踪 .worktree-cleanup-archive-20261010/ 均保持不动。 |
| G0-05 依赖顺序 | PASS | 快速线 #73 → #74 → #76 → #77 → #78 已合并。RAG 线按 #66 → #52 顺序集成；真实质量评测仍需批准价格/预算和账本证据。#69 独立施工。 |

## GitHub 状态快照

| PR | 当前 head / base 与状态 | Review / checks | 施工状态 |
| --- | --- | --- | --- |
| #73 | MERGED；PR HEAD d9c4d7886bd2b63685721375b2b798137df61804；Merge SHA ed90609c335753f0519add64442cf7271d75ad54 | 三条 Review 已回复并解决；当前 HEAD 的代码检查通过；真实 API Evaluation SKIPPED | MERGED |
| #72 | 原 PR 已 MERGED，Merge SHA 45b0a7be54eaf0a3b7df4676f9978b095236932f | 两条遗留 Review 已通过 #74 的证据回复并解决 | MERGED（遗留由 #74 修复） |
| #74 | MERGED；PR HEAD 179d90ecf1adf87540707e5f4832a542df02d1f8；Merge SHA 5b372540800f1b6689d371e08e79fff009200425 | Trip URL/正式行程投影；本地与 GitHub 相关检查通过；真实 Provider/RAG Evaluation 未执行且不计通过 | MERGED |
| #70 | 原 PR 已 MERGED，Merge SHA 5df8b0603355566e7fa1eb87906129c11a8211ab | 原 PR Review 0；跨会话唯一性走新 Follow-up #76 | MERGED（原 PR） |
| #76 | MERGED；PR HEAD d47f43f10bd5914b0f13140d5daad18ae5b0e5b4；Merge SHA `7350cfe8dfb50bb6617bccb386603a5428e7747e`；后续 #77 将 main 推进到 `b3e2fb8` | 合并前共享项目库 duplicate-group 只读统计为 0；隔离真实 PostgreSQL 迁移/并发回归 1 passed；合并后在精确 Merge SHA 复跑隔离真实 PostgreSQL 1 passed。真实 API Evaluation skipped。098 尚未应用到共享数据库 | MERGED（共享库迁移状态仍待核实） |
| #69 | OPEN、Draft；HEAD 8111c4574f1f696dadde03ad442bbd03f078c756；base e4881940；mergeState CLEAN | 当前 SHA 的 RAG Smoke、免费 preflight、contract-check、Detect、Secret Scan 全部 PASS；真实 API Evaluation SKIPPED。新增真实 PG 同用户跨租户物理隔离测试；用户/租户隔离 2 passed。T2 已有 SSE 69 passed、Conversation-first/产品边界 65 passed、Memory/身份/请求事件 57 passed；既有 DB Registry→Doubao 真实 chat 成功。AG-08 部分、AG-09 PASS；完整 RAG/SQL/取消/Trace/费用 T3 未完成 | IN_PROGRESS（继续授权 RAG/SQL 和流式端到端验收） |
| #66 | OPEN、Draft；HEAD `3a2ddf91d64cdd148379e1e07abe9d75d8e90091`；base `e4881940`；GitHub mergeState `CLEAN` | 当前 SHA 的 Detect/Secret Scan、RAG Smoke preflight 与 smoke wrapper PASS；真实 API Evaluation SKIPPED。testset PDF 在隔离环境完成真实 Upload→Celery Worker→IndexRun published→Registry active（42 chunks）→授权 Search，命中当前文档正文。单独分支调用尚未通过 #52 预算代理结算 | IN_PROGRESS（数据链验收通过；需与 #52 预算链安全集成后再合并） |
| #52 | OPEN、Draft、CLEAN；HEAD 91db7640cd83828af93aeeccdda65fa3b960a85c；base e4881940；14 条 Review 已解决 | GitHub 保护环境已配置 Doubao/Embedding/Rerank API secret 与对应模型变量；免费检查及本地定向回归 PASS，追加 113 passed。真实 8-case Evaluation SKIPPED。缺 `PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL`；历史隔离价格行币种错误，无当前价双审/24h canary | BLOCKED（价格治理/只读 Registry 外部门禁未完成；Provider 凭据不是阻断） |
| #50 | CLOSED | FE-4 已被 PR #72 覆盖；当前 main 对应三组定向前端测试 40/40 通过 | 已关闭过期 Draft |
| #60 | CLOSED | 文档中 T1 已由 PR #61 合入；独立作品集交接材料由 #75 交付 | 已关闭旧 Draft |
| #75 | MERGED；PR HEAD dba170373c4663919ab9b0e2b87b5f800d479310；Merge SHA 04846a61f889fa561b9c2d2184cd196c93e75dd5 | 合并后 3 条 Review 由 #78 修复；Follow-up 合并后已追加 Merge SHA 与验证证据并全部解决原线程 | MERGED（Review 收口完成） |
| #77 | MERGED；head `1093c39beb23b00d4210660eb877d062d49230e5`；Merge SHA `b3e2fb85937d011179ed046b6d264a7a0f3454d4` | 本地 42 passed，四条 Review 已修复/解决。当前 SHA Detect、Secret Scan、PR 免费 preflight 与 8-case Smoke 均 PASS；真实 API Evaluation skipped。main push 的 8-case Smoke 和 Secret Scan PASS，未运行付费 Probe | MERGED |
| #78 | MERGED；PR HEAD `4e1216884333917e392b3442c826500ce86981cd`；merge 前 base `b3e2fb8`；Merge SHA `e4881940f1d39f82d1f7a938562e0e611f6677bb` | 文档相对链接、源码路径和 diff-check 通过；PR Smoke/Detect/Secret Scan 与 main push 的 RAG PR Smoke/Secret Scan PASS；paid/preflight job 按文档工作流跳过；当前 Review threads 为空 | MERGED |

原 PR branch protection 核实为 Required Checks 为空、required approvals 为 0；此次未绕过保护。Skipped job 不计为通过。

## 合并历史

- #73 普通 merge：ed90609c335753f0519add64442cf7271d75ad54。
- #74 普通 merge：5b372540800f1b6689d371e08e79fff009200425。
- #75 普通 merge：04846a61f889fa561b9c2d2184cd196c93e75dd5。
- #76 普通 merge：7350cfe8dfb50bb6617bccb386603a5428e7747e。
- #77 普通 merge：b3e2fb85937d011179ed046b6d264a7a0f3454d4；后续 #78 将 main 推进至 e488194。
- #78 普通 merge：e4881940f1d39f82d1f7a938562e0e611f6677bb（当前 origin/main）。
- #72 与 #70 原始合并历史保留；后续修复均使用 Follow-up，没有改写旧历史。
- #50/#60 已关闭，未将过期 Draft 合入 main。

## 施工线状态与下一步

| 线 | 当前状态 | 下一项实际动作 |
| --- | --- | --- |
| A Provider/旅游/客服 | #73/#74/#76 MERGED；#75 Follow-up #78 文档修复完成 | #78 的旧自动 Review summary 没有公开具体意见，暂不合并或关闭原 Review；继续处理其他线。098 已进 main，仍需在获授权发布环境执行迁移。 |
| B RAG | #66 数据终态已验收；#52 价格门禁 BLOCKED；#77 MERGED | #66 Worker E2E 已通过，但其分支独立合并会使 Worker 调用缺少 #52 的有效预占/结算，暂不单独合并。#52 当前 HEAD 已修正 usage 与预算请求关联；8-case 真实评测仍受价格治理和只读 Registry 外部条件阻断。 |
| C 主助手 | #69 IN_PROGRESS | 当前 SHA 免费 CI 全 PASS；会话/事件与用户/租户隔离定向集已有 69+65+57+2 passed。下一步仍是隔离当前 HEAD 的真实 SSE、授权 RAG/SQL、Trace/账本和失败终态；真实检索/费用依赖 #52 权威价格快照，不能用共享旧容器替代。 |
| 过期 PR | #50/#60 已关闭；#75 已合并 | #75 三条新增 Review 由 #78 修复；合并前暂不解决原线程。 |
| main 集成 | IN_PROGRESS | MAIN-01～34 已逐项标注真实证据状态。当前共享容器 build SHA unknown，RAG `/readyz` 503，不能当作当前 main 验收；#52 GitHub 保护环境已具备 Provider API secret，但需配置经治理的只读价格 Registry 才能进行付费质量门禁。 |

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

当前 main：e4881940f1d39f82d1f7a938562e0e611f6677bb。已合并 #73、#74、#75、#76、#77、#78。MAIN-01～MAIN-34 总体尚未进行整体验收；已验证的局部链路仅按以上各 PR 记录。基础设施、全链路业务、共享数据库迁移、主助手真实 T3 和 RAG 真实质量未验证，不作上线就绪结论。

## 最近检查

- 原 main checkout 的用户变更和未跟踪目录保持原样。
- 控制工作树每次先核对 diff/status；不做全局 cleanup。
- 最近定向验证：#66 当前 HEAD 的上传/索引/Celery 集 36 passed、覆盖路由/质量门禁 27 passed、Worker/运行时/存储集 44 passed、页码保留 3 passed；#69 晚到事件隔离测试在 b3e2fb8 与 e488194 基线分别 1 passed，此前事件作用域回归 106 passed；#76 精确 Merge SHA 的隔离 PostgreSQL 回归 1 passed；#73/#74 后端 API/健康契约 37 passed（仅测试令牌）。#52 代码 HEAD 的付费预检、模型绑定、预算、工作流和路径过滤测试 45 passed。#66 新 SHA 的 Detect/Secret Scan PASS，RAG Smoke preflight 仍运行；#52/#69 以此前已核验 SHA 的免费 CI PASS，真实 API Evaluation 均 skipped。#66 真实 PDF E2E/Search、#69 真实隔离 T3、#52 真实检索与账本尚未完成；#76 迁移尚未应用于共享数据库。


## main 集成验收逐项矩阵（当前证据）

状态按当前 main e488194 及本轮实际证据记录；PR 分支上的通过不替代 main 或真实 E2E 结果。

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
