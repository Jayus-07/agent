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
| #69 | OPEN、Draft、CLEAN；HEAD `a06ebe1815d7f4a97b6c5eff27029c20bfddebc9`；base `e4881940` | 已同步 #78 后 main；Detect/Secret Scan PASS，contract-check 与免费 preflight 运行中；真实 API Evaluation SKIPPED。旧定向事件回归 106 passed；晚到事件隔离测试在合并 b3e2fb8 前后各 1 passed。真实 T3 未完成 | IN_PROGRESS（真实 T3 未验证） |
| #66 | OPEN、Draft；HEAD `d0d2d70164c73e801fd4084153738a1f5540648c`；base `e4881940`；mergeable | Worker 追踪修复本地 44 passed；页码歧义/正文保留回归 3 passed；py_compile/diff-check 通过。此前 HEAD 的 RAG Smoke、free preflight、Detect、Secret Scan 均 PASS；最新文档基线同步后的检查待更新。真实 API Evaluation skipped。目标旧任务仍无真实 IndexRun/Search 终态，旧 Worker/SIGKILL 日志已丢失 | BLOCKED（真实 PDF 终态未验证） |
| #52 | OPEN、Draft、CLEAN；HEAD `c37b0ede41ca9b5667e9188eb0db0722ff10d073`；base `e4881940`；14 条 Review threads 全部已解决 | Registry/预算门禁冲突已整合；本地合并相关定向集 45 passed，编译、YAML 解析和 diff-check 通过。前一代码 HEAD 的 preflight、Detect、Secret Scan、required `RAG Smoke (8 cases)` PASS；最新文档基线同步后的检查待更新。真实 API Evaluation 为 SKIPPED；缺获批价格/预算 | BLOCKED（付费 T3 需获批价格、预算与账本证据） |
| #50 | CLOSED | FE-4 已被 PR #72 覆盖；当前 main 对应三组定向前端测试 40/40 通过 | 已关闭过期 Draft |
| #60 | CLOSED | 文档中 T1 已由 PR #61 合入；独立作品集交接材料由 #75 交付 | 已关闭旧 Draft |
| #75 | MERGED；PR HEAD dba170373c4663919ab9b0e2b87b5f800d479310；Merge SHA 04846a61f889fa561b9c2d2184cd196c93e75dd5 | 合并后 3 条 Review 由 #78 修复；Follow-up 合并后已追加 Merge SHA 与验证证据并全部解决原线程 | MERGED（Review 收口完成） |
| #77 | MERGED；head `1093c39beb23b00d4210660eb877d062d49230e5`；Merge SHA `b3e2fb85937d011179ed046b6d264a7a0f3454d4` | 本地 42 passed，四条 Review 已修复/解决。当前 SHA Detect、Secret Scan、PR 免费 preflight 与 8-case Smoke 均 PASS；真实 API Evaluation skipped。main push 的 8-case Smoke 和 Secret Scan PASS，未运行付费 Probe | MERGED |
| #78 | MERGED；PR HEAD `4e1216884333917e392b3442c826500ce86981cd`；base `e4881940`；Merge SHA `e4881940f1d39f82d1f7a938562e0e611f6677bb` | 文档相对链接、源码路径和 diff-check 通过；Smoke、Detect、Secret Scan PASS；文档对应的 paid/preflight job 按工作流跳过；当前 Review threads 为空 | MERGED |

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
| B RAG | #66 BLOCKED；#52 BLOCKED；#77 MERGED | #66 的 PDF upload/IndexRun 仍无可核验终态；#52 等批准价格/预算与账本门禁；#77 已合入 `b3e2fb8`，PR 与 main push 的免费检查通过。 |
| C 主助手 | #69 IN_PROGRESS | 完成离线检查和可安全执行的合同验证；真实 Provider/权限/Trace/费用 T3 只能在凭据和隔离链可用时执行。 |
| 过期 PR | #50/#60 已关闭；#75 已合并 | #75 三条新增 Review 由 #78 修复；合并前暂不解决原线程。 |
| main 集成 | IN_PROGRESS | MAIN-01～34 已逐项标注真实证据状态。其余 PR 当前均有独立外部门槛；恢复凭据、Docker/Worker 或数据库只读权限后继续对应施工与集成验收。 |

## 外部阻断与诊断

### #76：退款订单唯一性迁移

- PR #76 已普通合并，PR HEAD d47f43f10bd5914b0f13140d5daad18ae5b0e5b4，Merge SHA 7350cfe8dfb50bb6617bccb386603a5428e7747e。迁移 098 已注册进 main；部署数据库是否应用仍未知。
- 合并前对项目数据库以只读事务执行 duplicate-group 计数，结果为 0；未修改共享数据库。不能把此结果解释为迁移已应用。
- 隔离 PostgreSQL 在当前 Merge SHA 上真实回归 1 passed（CS-12）；migration registration、唯一冲突/幂等/租户隔离等已有定向测试在 PR 合并前通过。
- 发布前需由有权限的操作流程在目标环境检查 098 应用状态并执行迁移；本任务不写共享/生产数据库。

### #66：PDF Worker 真实终态

- 当前 PR HEAD `d0d2d70164c73e801fd4084153738a1f5540648c`，包含 PDF 页码清洗修复 `96407e7` 和 Worker 追踪修复 `f3573f0`，并已同步 main `e488194`。页面数字保留定向回归 3 passed；Worker/运行时/存储集 44 passed；此前 PR 分支文档解析/coverage 集 77 passed。最新文档基线 SHA 的 CI 状态仍在核验，真实 API Evaluation skipped。目标上传 `b96f4d86ebe3`、IndexRun `f7a06549-ba71-4abf-a134-3a7b3d35deb1` 在只读隔离库中未终结，两个快照的 stage 不一致，不能推断实际终态。Celery active/reserved/scheduled 为空。原 Worker/Celery/Docker SIGKILL 事件日志不可用，不能断言 OOM。
- 当前 Docker Desktop 可用；主 `agent` Compose 有 16 个共享服务运行，容器总内存约 3.1GiB/8GiB，RAG worker 上限 3GiB。启动完整隔离解析链可能使内存接近上限，未启动以免影响共享任务。
- 只读启动并停止旧 `rag-pr55-e2e-evidence-db`：容器无网络/端口/bind mount，独占独立 evidence-copy 卷；该库只有 22 个其他 IndexRun，不含目标 upload；日志只有 PG 启动与诊断 SQL。另一个旧隔离源卷 `rag-pr55-e2e_pg_data` 的只读副本包含目标 upload/IndexRun。原 worker 容器已不存在，故无法恢复 SIGKILL 信号、memory event 或进程日志。
- RAG-03 PDF 最终 IndexRun、RAG-08 Search 未完成；RAG-04/09 部分或未验证；RAG-12 未验证。此前“修复已推送 HEAD 96407e7”的记录不准确：远端分支 HEAD 是 32f8406，修复提交是其祖先，故修复确实包含在 PR 当前 HEAD。
- 对 `rag-pr55-e2e_pg_data` 按 Compose 标签确认属于旧隔离项目，原卷未被活动容器挂载；只读副本两次快照均显示目标 upload `b96f4d86ebe3`、IndexRun `f7a06549-ba71-4abf-a134-3a7b3d35deb1` 未终结，但 stage 分别为 `table_describe` 与 `indexing/index`。generic task `0f567e80-e6e7-45e1-aaa4-c981f104e0a6` 为 RUNNING，Celery ID `737ad57f-b12c-4cdf-b7ea-8741decc7f93`，worker 为空，recovery_count=0，租约已过期；Redis 持久化卷中找不到该 Celery ID。原 Worker/Beat 容器、SIGKILL、memory event 与日志均已丢失，不能归因为 OOM 或断言具体退出原因。
- 本轮确认的代码缺口：上传路由在 Celery `apply_async` 成功后只回填 generic `tasks`，没有回填 `rag_index_runs.celery_task_id`；Worker 租约认领固定传 `worker=None`。已在 #66 修复并推送 `f3573f0c68f41a28a076b36d5bc0be1bbdbd4236`：运行记录回填 Celery ID，普通索引和重索引都把 Celery hostname 传到既有租约字段；回填失败记日志且不重复投递。
- PR #66 HEAD 定向运行 `test_rag_upload_celery_mode.py`、`test_index_task_runtime.py`、`test_upload_failures_store.py` 共 44 passed，使用隔离 PostgreSQL 副本；py_compile、diff-check 通过。Windows Proactor teardown 有 2 条 warning。恢复并发单测 `test_case_d_concurrent_sweep_single_recovery` 单独重跑 1 passed；两文件恢复套件的一次整组执行未得到 pytest 汇总，不计为通过。临时副本容器/卷已清理，源卷仍保留。

### #69：主助手真实 T3

- 当前 PR HEAD `a06ebe1815d7f4a97b6c5eff27029c20bfddebc9`，已包含 main `e488194`。已完成 Product/Tenant/User/Session/Request 事件作用域、晚到事件 fail-closed、安全元数据；定向事件作用域回归 106 passed，晚到事件测试在两个 main 基线各 1 passed。新 SHA 的 contract-check 与免费 preflight 尚待完成；真实 API Evaluation SKIPPED。
- 隔离树缺少安全注入的 BFF API_KEY、Provider 凭据/Registry 角色和隔离 APISIX/账本链。Docker 可用，但默认 Compose 是共享栈且资源余量有限，未启动隔离全链。未复制生产密钥、未触碰共享数据库、未付费调用。
- AG-09、AG-14/15 完整权限链路、AG-18/19 真实 Trace/费用、AG-20 真实 Provider 错误终态仍未验证。

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
- 当前代码 HEAD `8297b0f` 的集成冲突相关定向测试 45 passed；该 HEAD 的 GitHub preflight、Detect、Secret Scan、required `RAG Smoke (8 cases)` PASS；同步 #78 文档基线后的新 SHA 检查仍在运行。真实 API Evaluation 为 SKIPPED_UNTRUSTED_PR。
- 当前 HEAD 的真实检索质量和费用账本未验证。旧 SHA Top-1 0.7143 低于 0.80，不能作当前结果。当前无可恢复的已批准价格快照与评测预算，未授权再次发起 Provider 调用；等待价格与预算门禁后再执行真实评测。

## main 集成验收状态

当前 main：e4881940f1d39f82d1f7a938562e0e611f6677bb。已合并 #73、#74、#75、#76、#77、#78。MAIN-01～MAIN-34 总体尚未进行整体验收；已验证的局部链路仅按以上各 PR 记录。基础设施、全链路业务、共享数据库迁移、主助手真实 T3 和 RAG 真实质量未验证，不作上线就绪结论。

## 最近检查

- 原 main checkout 的用户变更和未跟踪目录保持原样。
- 控制工作树每次先核对 diff/status；不做全局 cleanup。
- 最近定向验证：#66 代码 HEAD 的 Worker/运行时/存储集 44 passed，PDF 页码歧义保留 3 passed；#69 晚到事件隔离测试在 b3e2fb8 与 e488194 基线分别 1 passed，此前事件作用域回归 106 passed；#76 精确 Merge SHA 的隔离 PostgreSQL 回归 1 passed；#73/#74 后端 API/健康契约 37 passed（仅测试令牌）。#52 代码 HEAD 的付费预检、模型绑定、预算、工作流和路径过滤测试 45 passed；其免费 GitHub checks 全部 PASS，真实 API Evaluation skipped。#66/#69/#52 已再同步 #78 文档基线，新 SHA 的 CI 结果待完成；#66 真实 PDF E2E/Search、#69 真实隔离 T3、#52 真实检索与账本尚未完成；#76 迁移尚未应用于共享数据库。


## main 集成验收逐项矩阵（当前证据）

状态按当前 main e488194 及本轮实际证据记录；PR 分支上的通过不替代 main 或真实 E2E 结果。

| ID | 检查项 | 状态 | 当前证据 / 缺口 |
| --- | --- | --- | --- |
| MAIN-01 | APISIX 正常 | BLOCKED | Docker Desktop 已运行；默认 `agent` Compose 是共享的 16 服务栈，未启动隔离 APISIX 验收链，避免资源争用。 |
| MAIN-02 | FastAPI 正常 | BLOCKED | 通过定向 TestClient 合同测试；真实 APISIX→FastAPI 服务链未启动。 |
| MAIN-03 | Next.js 用户端正常 | BLOCKED | 主控隔离树无 frontend/node_modules；不重复安装，未启动页面。 |
| MAIN-04 | 管理端正常 | BLOCKED | 未启动管理端；当前任务树无已装前端依赖。 |
| MAIN-05 | PostgreSQL 与 Redis 正常 | NOT VERIFIED | 独立 PostgreSQL 用于退款并发回归；共享 Compose 内 PG/Redis 正在运行，但本轮未对其做全链健康验收或写入。 |
| MAIN-06 | Celery Worker、Beat 和消费 | BLOCKED | 共享 Compose 服务存在；目标 PDF Worker 容器和任务事件不可恢复，未启动额外 Worker 以免挤占 8GiB Docker 资源。 |
| MAIN-07 | 健康及就绪接口 | NOT VERIFIED | 未在集成服务上请求健康/ready 接口。 |
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
- PR #66 当前 HEAD `d0d2d70164c73e801fd4084153738a1f5540648c`，已包含 main `e488194`。Worker 追踪修复后 44 个相关测试通过，页码保留 3 项通过；新文档基线 SHA 的 CI 仍待更新。目标旧 Worker 日志和 memory events 不存在，不能还原 SIGKILL 原因；真实 PDF E2E 仍未执行。
- PR #52 当前 HEAD `c37b0ede41ca9b5667e9188eb0db0722ff10d073`，已同步 main `e488194`；14 条 Review 全部已解决，代码集成定向测试 45 passed。前一 SHA 的免费 checks 全部 PASS；新文档基线 SHA 的 CI 仍待更新。真实质量评测和账本仍需获批价格/预算快照；不发起付费调用。
- PR #78 已合并为 main `e488194`；Follow-up 修复的三条 #75 原始 Review 已附合并 SHA/验证证据并全部解决。

### RAG Smoke 意外真实 Provider Probe 事件

- PR #76 合并触发 main-push RAG Smoke 工作流 `38787751555`；旧路径过滤把普通迁移 `098_cs_refund_order_unique.sql` 当成 RAG 变更，且旧 workflow 在付费 Probe 前无批准价格快照门禁。尝试取消时 Probe 已执行；之后 fixture 导入取消、Index/Search/8-case 评测 skipped，整个工作流为取消/失败，不能记为 PASS。
- 脱敏账本汇总显示 2 次 Provider 调用、共 2,335 tokens，均为 `unpriced`，`billed_cost_cny` 为 NULL，实际金额未知。不能声称没有 Provider 调用或声称已知费用；完整 8-case 质量评测没有运行。此次没有再触发 Probe。
- 后续 #77 区分 push 与 PR 静态变更，并在任何模型 Probe 前验证 Registry 批准价格。HEAD `1093c39` 本地路径判定为 `relevant=false`；GitHub 免费 PR 检查和 main push 的 8-case Smoke / Secret Scan 均通过，真实 API Job skipped。该修复已随 `b3e2fb8` 合并。

### 当前阻断和下一项施工动作

- #75/#78：Follow-up #78 已合入 main `e488194`；三条旧 Review 均已追加证据并解决，收口完成。
- #77：已于 `b3e2fb8` 合并；当前 main 为 `e488194`，main push 的免费 smoke/scan 已验证，无付费 Probe。
- #66：新 SHA 的免费检查通过、无 Review；继续确认能否通过既有授权凭据安全执行隔离 PDF E2E。若无获批 Provider/隔离服务，则保留 Draft/BLOCKED，继续其它 PR；不得在共享栈上重放或发起未经批准的付费调用。
- #69：继续离线合同与安全边界验证；真实隔离凭据/服务/账本仍未具备时保持 Draft，不影响其它 PR。
- #52：价格 Registry 冲突已解决并推送；代码 SHA 免费 CI 已通过，最新文档基线 SHA 等待 CI。无获批价格快照和预算时不执行真实质量评测，付费 T3 未验证前不合并 Draft。
- #76：代码已合并；098 在目标共享数据库的应用状态交给获授权部署/迁移流程确认，本任务不写共享或生产库。
