# 进度

## 已完成

- 任务分支：`codex/enterprise-assistant-roundtable-sse`；基线 HEAD：`d886f7c`。合并前发现本地 `main` 已前进到 `c3e1f97`，已确认其提交不改本任务文件，并将任务分支快进至该 HEAD 后再构建。
- `RequestBudget` 继续累计调用数，但不再因 `max_calls` 拒绝模型调用；token、金额、重试和备用模型限制保持现有行为。
- 仅 `QuotaExceeded.subject_id` 为 `user:*` 时从 GraphRunner 发出 `paused` 终态。暂停帧保留 SSE 已发出的部分文本；租户额度、配置错误等仍走错误协议。
- 前端将 `paused` 视为终态，保留过程快照与部分回答，并提供补足余额后重新发送。会话历史、模型选择、预算展示、停止/重试和功能导航继续使用企业助手现有 API/状态能力。
- `/agent` 已改为独立的 `AgentWorkspace` 展示实现；不接圆桌 API，不导入原 `/agent` 展示组件。圆桌原型只借用过程时间线、回复气泡、处理中指示和暂停提示的表现方式。
- 同步 SSE 生命周期、前端 SSE 与模型预算文档。

## 验证

- `frontend`: `npm ci --offline` 成功；`npm run build` 成功（Next.js 15.5.28，类型检查与 16 个静态页面构建通过）。构建自动改写的 `frontend/next-env.d.ts` 已恢复。
- 以本工作树的生产构建在 `localhost:3101/agent` 实际打开页面；新建页可渲染，输入文字后发送按钮启用，清空后禁用。未提交真实模型请求，避免产生业务调用或费用。浏览器控制台无错误；历史/额度 API 由于隔离运行缺少 `X-API-Key` 未能验证。
- 对 5 个改动的 Python 文件执行 `D:\Python\python.exe -m py_compile`，退出码 0。
- 从快进后的任务工作树执行 `devctl.bat rebuild /y` 成功；11 个后端镜像构建成功，数据库迁移器退出码 0。`agent-app-1`、worker、RAG 和 business-mock 为 healthy；`/health` 与 business-mock `/health` 返回 200。
- 在新 app 容器内做无 Provider 调用的运行 smoke：`max_calls=1` 时 reserve 9 次均通过且无 exceeded；`user:*` 判为暂停、`tenant:*` 不判为暂停；`paused` 帧通过契约校验。
- 未执行测试套件，也未调用真实模型，因此真实用户欠额到浏览器暂停 UI 的完整 SSE 链路仍未端到端演练。
- `git diff --check` 通过。

## 后端运行验证状态

- 用户授权后，已从本任务工作树重建共享后端；服务健康检查通过。后端新代码已部署到本机服务。
- Provider 级余额耗尽的完整 SSE 真实请求未触发，以免实际调用模型或改变额度；验证范围仅到预算拒绝、作用域分类、暂停帧契约和服务健康。

## Checkpoint 语义

- 企业助手聊天记录有服务端会话持久化；主图执行 checkpoint 当前默认关闭且没有主图恢复入口。`/chat/agent/stream/resume` 仅重放进程内 SSE 事件，不会从中断节点续跑。
- 因此余额补足后重新发送原问题；当前暂停能力不宣称恢复之前的 graph 执行。

## 2026-10-26：4C8G 容器配置与全量重建准备

- Worktree：`C:\Users\wh\.codex\worktrees\assistant-roundtable-sse\agent`；分支 `codex/enterprise-assistant-roundtable-sse`；HEAD `41f69a7bf39f3fa024a2ebcef565e8b0fd33c6e2`。本轮配置和文档改动尚未提交。
- 数据库只读确认 embedding=`bge-m3`、rerank=`bge-reranker-v2-m3`，供应商均为 `custom-gpu`、billing=`local`、地址 `http://host.docker.internal:8088`。宿主机 Python PID 3688 健康接口返回 embedder/reranker 已加载，当前进程工作集约 1.2GiB；GPU 为 GTX 1660 Ti 6GiB，总显存使用约 2.3GiB。这些不是重建时峰值。
- 普通索引 worker 显式注入 `EMBED_BATCH_CONCURRENCY=1`；全量 `run_rebuild` 源码实际是最多 8 条/批、串行生成，不受此参数控制。Agent worker 并发 2、索引 worker 并发 1、维护/报表各 1。
- dev-svc.bat 在未显式指定 COMPOSE_FILE 时自动加载根 Compose 与本机覆盖层。保留脚本 ASCII 编码。维护 worker 从覆盖层原 224m 调整为已运行的 512m（常驻实测约 280MiB）；索引 worker 为 1024m。
- 从本 worktree 执行 `devctl.bat rebuild /y` 成功。实际容器标签确认加载两个 Compose 文件；app=1000MiB/1.5CPU、Agent=768MiB/1CPU、索引=1024MiB/1CPU、维护=512MiB/0.3CPU、报表=768MiB/0.5CPU、RAG=512MiB/0.5CPU。检查的容器全部 healthy、OOMKilled=false、RestartCount=0；8000/health、8090/readyz、8091/health、9080/health 均 200。`git diff --check` 通过。
- 当前非候选向量 7114 条、19 个集合（包括保留的 eval 集合）。没有 biz_type=embedding_rebuild 的任务。未删除任何集合，未提交全量重建，也未改写向量。
- **BLOCKED：管理端按钮及重建负载验收。** 3200 的旧 Next dev 进程 PID 30344 页面请求超时；自动审批分别拒绝停止旧前端并启动当前 worktree 前端、以及在 3201 启动备用前端，理由均仅为 blocked by policy。没有绕过审批；浏览器读取也未能取得页面。需恢复进程操作能力或由用户恢复管理端后，继续按钮预检/确认、监测完整重建、核实模型指纹与退款问答。未完成 T2 真实重建/问答验收，不把当前配置认定为已通过完整负载调优。

## 2026-10-26：生产全服务资源档与真实按钮重建（续）

- 用户明确模型服务外置。统一使用 local 命名的共享资源层；生产层仅替换后端 env_file 为 .env.production。常驻服务上限合计 7168MiB（含三个前端、单 dispatcher、备份），为 8GiB 系统预留 1024MiB。开发 mock 额外 128MiB，不在生产清单内；重型可选 profiles 不启动。
- 所有 20 个本机运行容器已落实 cpuset=0-3、对应内存/CPU配额；DB 池1–2、全局请求闸门12、PG120连接/256MB shared_buffers/4MB work_mem、Redis按缓存/队列/认证分配并预留AOF空间。Redis加载AOF实测36秒，已增加60秒启动宽限。
- devctl rebuild 完成后端与共享数据库/缓存重建；其余基础设施用本 worktree Compose重建。三个前端镜像分别构建成功（管理端47页、用户端16页、客服10页），改用Node22与standalone容器运行；各256MiB。旧宿主前端已通过用户重新授权的devctl停止，管理端API Key缺失问题通过BFF运行时注入恢复。Docker前端start/stop入口增加容器识别，避免误杀Docker端口转发进程。
- 管理端已恢复既有超级管理员会话并显示13个角色。当前模型为 bge-m3/custom-gpu，预检确认1024维、19集合7114条向量。通过页面“检查并重建全部向量”及“确认并开始重建”真实提交任务，未直接调用API或改数据库状态。
- 任务ID：`a1f8912b-d120-48e0-a1dd-13523f86b1d5`；Celery ID `f98aad99-df79-4eeb-b5a2-b69d5f07846b`；2026-10-26 12:23:30 UTC创建。状态IN_PROGRESS：RUNNING，已见1536/7114生成，无错误。持续采集D:/tmp下本轮agent-4c8g-probe JSONL资源数据；目前采样总内存峰值约3.0GiB。候选未全量发布前不认定索引成功。
- 备份首轮agent_memory约71MB、agent_business约156KB，日志均OK，pg_restore -l目录可读；尚未做隔离恢复演练。PgBouncer SELECT1、三个前端/login及后端健康检查通过；截至提交任务无OOM或重启。仍须跟踪任务终态、核实所有集合身份与向量数量并验证企业助手问答。
- 首次任务在2800条后FAILED/provider_timeout，正式向量未发布，2800条候选保留。错误栈确认OpenAI embedding HTTP读超时；失败批包含一条16002字符文档，现有客户端沿用在线8秒超时并重试一次。修复为仅run_rebuild固定客户端采用独立60秒超时，在线默认保持不变，非法无限/非正超时拒绝；队列可见性3900秒覆盖3660秒全量任务硬时限。定向超时用例6 passed，Python语法检查通过；需重建后从管理端任务中心重试原任务验证长文档和候选复用。
- 完整定向验证：embedding_singleton、embedding_switch、devctl migration runner共20 passed。旧provider测试的SimpleNamespace缺少身份字段，替换为正式SpecializedBinding并增加非空指纹断言，未删断言或跳过测试。环境缺pytest-timeout插件产生2条配置警告，测试本身均执行通过。
- 60秒离线超时与3900秒队列可见性已从当前worktree重建并在容器环境确认；冷启动超过devctl短等待窗口，随后实际8000/health、8090/readyz、9080/health均200，索引worker healthy，候选仍2800条。企业助手用户已在页面重新登录，管理员会话随后过期，已请求用户在3200重新登录。
- **最新状态BLOCKED（覆盖上文运行态描述）：** 2026-10-26约20:56本地时间，Docker Desktop被重启；恢复后另一操作从`D:\Program Files\workplace\agent`主目录重建共享栈，并启动新的主目录Next管理端进程。实际app/index-worker工作目录标签均变为主目录，内存又为4GiB/3GiB，cpuset为空，本任务新修复不再是当前后端运行版本。主目录还有未提交DatasetsTab与tsconfig等改动，未覆盖或提交它们。已向用户询问并行操作安排，未再次覆盖新栈、未直接改任务状态。
- 原任务`a1f8912b-d120-48e0-a1dd-13523f86b1d5`仍FAILED/provider_timeout，持久候选2800条确认保留，正式向量尚未发布。首轮资源采样116次，总内存采样峰值3153.173MiB；这是失败尝试的测量，不能报告为全量成功验收。采样进程已正常结束。
- 下一步：确认其他共享栈重建已停止并恢复管理员登录；重新从本worktree部署资源层及60秒离线客户端，核对容器标签/配额后，通过管理端任务中心重试原任务（复用2800候选），跟踪7114条全部发布，再核对19集合模型/供应商/指纹并完成已登录企业助手退款问答。本轮配置、Python修复、测试与文档均未提交；HEAD仍41f69a7。

## 凭据登录与继续执行（2026-10-26，覆盖前述BLOCKED状态）

- 用户提供临时管理员凭据文件并授权自行登录。凭据只在浏览器内存用于登录，不打印、不复制到代码或日志；原localhost开发页CDP无响应，改用同一Edge浏览器的127.0.0.1:3200生产前端入口，确认模型页和任务中心可访问。凭据文本、分行缓存和字段变量均已清除。
- 主目录栈稳定运行24分钟且无活动索引任务后，按既有授权从本worktree重新部署资源层/修复；保留主目录未提交文件及其宿主管理端进程。另将已运行的可选12306适配器限制为128MiB/0.2CPU/cpuset0-3，生产默认不启用该profile，不计入7168MiB基线。
- 通过任务中心筛选原任务并确认重试。期间新增评测数据，最新正式向量范围变为7613条；2800候选复用，模型/供应商指纹仍匹配。60秒请求策略仍在同一混合长文本批失败，GPU实测100%/5888MiB(总6144MiB)，确认应用容器无OOM；SDK自动重试会叠加客户端已超时而供应商仍计算的请求。
- 离线客户端进一步改为模型HTTP批大小1、SDK max_retries=0；外层8条候选检查点不变。在线客户端默认超时/批大小/重试不变。24个定向用例全部通过，语法检查通过。从本worktree再次devctl rebuild后，8000/health、8090/readyz均200，索引worker healthy且环境为batch1/timeout60/visibility3900。
- 已校验并重启本机8088的原模型进程以清除残留推理，使用原脚本路径和127.0.0.1绑定，未修改其他worktree源码；新PID26464，embedding=cuda、reranker=cpu，模型均重新加载、healthz正常。通过管理端再次确认原任务重试，实际已越过原失败点，进度2896、2952/7613；模型HTTP持续200，显存约2958MiB，仍在运行。当前状态IN_PROGRESS，尚未认定全量发布成功。
- 后续进度已到4000/7613，错误消息为空，未再出现超时；原任务ID始终不变。当前工作区部署已恢复，索引worker=768MiB/1CPU/cpuset0-3，API/PG/RAG等均healthy、无OOM或重启。新一轮资源采样118次时采样总内存峰值3082.663MiB。管理端在127.0.0.1:3200/tasks按完整任务ID筛选，便于继续观察和最终截图。

## 2026-10-27：全量发布成功与重建交互补强

- 覆盖此前IN_PROGRESS：原任务 `a1f8912b-d120-48e0-a1dd-13523f86b1d5` 已 `SUCCESS`，progress=`索引完成（done）`，output.vectors=7613，Trace=`b5c41cca156a`。21个正式集合全部ready，模型bge-m3、供应商custom-gpu、64位配置指纹一致。正式表7613行全部记录本任务ID及同一模型指纹，候选向量0行；不是只依据页面或HTTP成功推断。
- 重建以候选区完成全量校验后在同一事务覆盖正式行embedding及模型身份，再清理候选；原文、ID、集合归属保留。成功后不保留独立旧向量版本；失败保留正式向量，不能把保留等同于可用或成功后可一键回滚。已同步现行rag.md。
- 成功轮资源采样288次，总内存采样峰值3180.806MiB（包括开发mock/可选适配器，不含宿主外置模型）；采样已结束。API/索引worker/RAG服务均healthy、OOM=false、restart=0、cpuset0-3。未做全业务并发压测或备份隔离恢复演练；不能据此承诺任意负载最优。
- 用户要求管理端不能干等：模型页补充真实条数/百分比及原生进度条、后台执行/可离页返回说明、100%仍待发布说明、失败保留与任务中心重试入口。沿用正式状态API及每4秒查询，不新增执行链路。29个RoleBindingsTab定向测试通过（包含返回恢复、100%未发布、失败状态），管理端47页生产构建通过，已从本worktree重建frontend-admin容器并healthy。
- **企业助手问答验收未通过，不能宣称整体恢复。** 原问题“退款流程是什么？”Trace=`60a02d77c4e9`要求在rag.search/web.search间澄清；点击rag.search后未成功执行。明确知识库请求Trace=`5255dd03e8ab`进入general_chat，元数据domain=general/source=product_scope，候选为空、block_reason=no_authorized_candidates；本轮未调用检索，因此不能归因于重建向量失败。下一步定向核对主助手产品域分流和授权候选过滤；不绕过权限或擅自扩大用户权限。本轮未修改这段路由代码。
- 本轮改动保持未提交，HEAD仍41f69a7；无新PR/Review，远端生产服务器未实际部署。配置/索引发布PASS；企业助手问答FAIL，整体仍未完成该项验收。

## 2026-10-27：退款问答路由及父子超时对齐（续）

- 用户授权继续修复。已复现两处失败：引擎`_assemble`把能力候选缩成仅name/score，`CapabilityScore`也不声明风险/白名单/权限字段，经过JSON和V2投影后selector二次门禁必然误拒绝；显式“企业知识库中检索退款处理流程”缺少knowledge域的“知识库/检索”词声明，仅命中一个“流程”弱信号，语义分类可能落入客服域再被产品隔离映射为general。
- 修复：在正式候选模型及引擎组装保留risk/fast_path_enabled/permission_ready（缺失默认UNKNOWN/false/false）；路由缓存v4→v5避免旧缺字段缓存；manifest知识域增加“知识库/检索”，未修改产品隔离、用户权限、Tool Governance或执行门禁。JSON→V2→selector真实组装回归在修复前失败，修复后可用候选通过、缺字段/权限false/白名单false/HIGH风险仍阻断。显式知识库问句回归修复前失败、后通过；退款申请+订单仍识别客服域。
- 定向回归：selector/域分类/投影/引擎/适配器/产品隔离128 passed；补充missing字段及Runtime契约后selector+runtime 49 passed（与前一组有重叠）；缓存版本变更后引擎15 passed。语法和diff检查通过。现行ai-runtime.md同步候选安全字段传递契约。
- 首次真实页面修复验证Trace=`d7d2e90f3b88`：selected=rag.search、source=passthrough、reason=validated_hierarchical_fast_path，门禁不再误拒绝；但父请求仍19.2秒timeout。其RAG子Trace=`c6101be87fee`约25.95秒实际返回带出处的答案，确认父任务25秒图预算减预留仅剩约19秒，短于既有45秒RAG策略，导致成功答案被丢弃；子服务另有CPU重排供应商4秒超时并降级，未伪装为重排成功。
- 4C8G共享资源层与生产模板对齐BUSINESS_DEADLINE_S=60、WORKFLOW_DEADLINE_S=55、REPORTER_RESERVED_S=5；现有rag.search45秒/零重试/并发12及证据校验不变。读取真实Compose资源档的定向预算用例和既有超时回归7 passed，覆盖26秒响应能装入预算、45秒上限和预算过期拒绝。README同步依据和边界。从本worktree devctl rebuild成功，实际API/RAG均healthy，app环境60/55/5，候选字段及缓存v5已在容器确认。正在重新通过页面验证最终回答，尚未提前认定整体通过。
- **本轮验收PASS（覆盖上句待验收）：** 页面重新创建会话，原问题“退款流程是什么？”Trace=`4341488e6554`，status=success，总耗时36.829秒、直接执行26.5秒，返回FAQ退款申请步骤及来源；tool_selection=validated_hierarchical_fast_path/rag.search，无selection_block_reason。截图`D:/tmp/agent-refund-routing-fixed.png`。
- 再新建会话验证此前误入general的显式知识库请求，Trace=`a3c67a61efe9`，status=success，总耗时13.518秒，domain=knowledge，FC在rag.search/web.search两候选中选中rag.search，返回退款规则及制度/FAQ出处。截图`D:/tmp/agent-explicit-knowledge-routing-fixed.png`。两轮均完成SSE终态并恢复发送输入框，不只依据底层RAG返回成功。
- 本次T0语法/Diff检查通过，T1/T2定向路由/候选边界/产品隔离/超时回归及真实页面请求通过；未执行T3全量回归或并发压测。局部Diff自查保留保守默认、分数/margin/风险/白名单/注册/权限二次门禁及Tool Governance；没有修改用户授权，也没有放开专属产品域。已知外置本机CPU重排有供应商超时降级，仍是性能限制，本次未修改其他worktree模型服务源码或伪造重排成功。当前API/RAG健康、无OOM或重启，向量重建任务仍SUCCESS/7613。未新建PR、未提交或合并；HEAD仍41f69a7。

## 2026-10-27：问答延迟优化实施与运行时恢复阻塞

- 用户授权实施建议。本worktree参考模型服务增加GPU float16重排、小批次默认2、全部候选保序回填后排序、非有限分数拒绝；embedding精度/模型/归一化和512 token重排截断不变。共享推理锁限制双模型并发激活显存。默认auto有CUDA使用GPU，无CUDA使用CPU；可显式RERANKER_DEVICE=cpu，加载失败不会伪装就绪。未修改其他worktree源码。已确认原8088进程后替换为本worktree由dev-local-models管理的新进程PID3920，实际health显示cuda:0/torch.float16、双模型预热成功。
- 六条同输入长文本基准：原CPU14.237秒；GPU3.369/2.852/2.867秒，全部6条候选排序一致，最大分数差0.000079。结果保存D:/tmp/agent-rerank-cpu-baseline.json及agent-rerank-gpu-optimized.json；观测显存3423MiB/6144MiB。这只是小样本数值/延迟验证，不代表全部问答或并发质量通过。
- DashScopeReranker传输超时/连接失败不再自动重发；RagRequestState按供应商/端点/模型记录本轮失败，后续纠错不再重复提交同一服务，下一请求重新尝试。保留既有降级、证据与Claim校验。增加超时/连接错误及请求隔离回归。
- SSE复用原有log/replay缓冲：入口显示准备/检查请求，远端RAG结果实际到达后显示整理回答；等待文案改为处理请求，不伪称已经开始生成。Runner metadata.entry_timing记录before_trace_ms和worker_launch_ms，用于入口耗时分解。未在证据核验前输出模型答案。现行rag.md和commands.md同步。
- 定向测试首次暴露提前Runner yield会改变关闭生命周期，已将入口事件移到API producer并保留原Runner收尾。剩余旧断连测试要求首帧只能status/delta，但当前假图合法首帧为reporter/summary日志，已按具体来源和char_count校验后继续断连，未删除取消、无记忆写入、恰好收尾一次断言。重排/降级/Runner终态/HTTP流组71 passed+1旧首帧断言失败；修正该契约后Runner收尾+HTTP流25 passed（覆盖原失败），其他47项此前已通过。语法/diff检查通过。
- 后端从本worktree devctl rebuild已完成，用户前端16页生产构建完成并重建容器；API/RAG/用户前端及主栈其他代码服务健康。索引任务仍SUCCESS/7613，没有重新写向量。构建期间Docker响应变慢，结束后大部分服务恢复，但APISIX运行状态异常：Docker标记running/PID63664，docker-desktop内该PID已不存在，9080健康请求超时；restart及单容器重建恢复操作均报tried to kill container, but did not receive an exit event。未删除卷或改数据，未擅自重启Docker Desktop。
- 当前BLOCKED：真实页面最终性能验收。页面已回登录，保存的原用户凭据可用于正常登录，但网关故障阻断。已向用户发出重启Docker Desktop确认，因会影响其他会话容器（另见新增agent-mcp-preview-app），需确认后恢复。下一步：用户允许后重启Desktop、devctl start/健康检查、原用户正常登录、重新跑两条退款问题，读取父子Trace确认耗时/重排成功/纠错次数与入口反馈，再给最终性能结论。当前只确认模型基准优化与工程回归，不能宣称整轮问答已加速到目标。未提交，HEAD仍41f69a7。

## 2026-10-27：Docker Desktop / WSL 完全退出后恢复（覆盖前述环境阻塞）

- 用户确认已 Quit，并要求完全退出 WSL。已停止本次 Docker GUI/backend/启动等待进程，执行 wsl --shutdown，确认当时无运行发行版。日志先后定位 Docker/run/dockerInference 与 docker-secrets-engine/engine.sock 的残留 AF_UNIX 端点无法移除；不能据此归因为内存不足。
- 校验目录范围和重解析属性后仅重命名备份运行时目录：LocalAppData/Docker/run.stale-20261027-061423、run.stale-20261027-061627、LocalAppData/docker-secrets-engine.stale-20261027-061627。后者仅含0字节 engine.sock。没有恢复出厂、注销 WSL、删除数据库卷或清理其他工作区源码。
- Docker Desktop 重新启动后 docker info 返回 ServerVersion=29.2.1。从本任务 worktree 执行 devctl.bat rebuild /y 返回0，依赖层命中缓存；容器标签确认 agent-app-1 的 working_dir 为本 worktree。API /health、APISIX、用户页面 /agent 返回200；PostgreSQL accepting connections 且只读查询 public 表数60。此查询不等于全部数据完整性校验。
- API、RAG、MCP、Agent/索引/报告 worker、三端前端、Redis、PostgreSQL 健康；维护worker及business-mock最初仍启动中，business-mock健康接口已200。外置模型 healthz 为ok，embedder/reranker已加载，重排cuda:0/float16。
- 恢复后重新执行 test_chat_stream_hardening.py + test_stream_latency_tracker.py：25 passed；两个既有pytest-timeout配置警告。此前数据库不可达导致的用例初始化阻塞已消除。未再次重建向量；整轮问答质量和全部耗时类别验收仍待完成，不能由容器恢复推定通过。保持未提交。

## 2026-10-27：恢复后分项验收、生成观测纠错与测试资料归档

- 本轮范围：真实用户页面验证首反馈/完整回答、RAG/普通解释/联网；管理员账号单独验证 SQL 服务端路径，保持原业务账号权限不变。没有创建 PR 或提交，HEAD=41f69a7。
- 普通解释「用一句话解释向量检索，不需要联网」Trace=505d5659ff99，完整1.803秒；页面在点击后379ms观测到真实准备日志。原退款首轮恢复后Trace=d376e09ad927，总20.335秒（HTTP20.801秒），before_trace4044ms/worker_launch4614ms；RAG子f416536da492成功6.543秒，但返回种子文件里的矛盾金额，不能算业务质量通过。
- 显式知识库问题Trace=6ab92c6722e9，总14.354秒；子c5e921203bc6检索/重排通过而生成拒答。发现原生成span读取父ContextVar，误把前一阶段78个输入token当作生成用量。新增LCEL真实链回归在修复前失败；模型步在请求态携带本次代理元数据和generation_input_chars，清空/恢复本地ContextVar，未改统一账本结算。覆盖stream/invoke、缺usage不能沿用旧值、新请求空态。真实复测父47361ad36a8f/子5798602952c5证明实际生成输入2679/2022字符、1718/1270 token；模型仍自报无证据，不能再归因为提示词未带资料。此前78token推测已被纠正。
- 明确联网问题修复前Trace=7b78a7cd73fe，粗分类unknown直接澄清。manifest补肯定联网与搜索动作信号，否定联网不构成强信号；web.search描述允许用户明确指定联网，路由缓存v6；没有绕过主体/产品权限。新增正例修复前失败、修复后通过。真实149e3fdff02e选中web.search，但833token结果被默认768上限压为无法展示的预览。4C8G共享档TOOL_INLINE_MAX_TOKENS=2048，保持8192总上下文硬门禁和原并发；最终ead8106ef911总4.398秒，页面展示5条完整链接结果。该结果仍是原始搜索列表，未按用户要求限定官方文档或两句整理，联网质量验收未通过。
- 原业务账号SQL Trace=749f2793213f正确permission_denied，未扩大权限。用户提供管理员凭据文件后，管理端已有有效登录；独立企业助手UI拒绝非11位数字管理员账号，已清空密码并关闭临时页，不修改登录校验。使用正式管理端BFF登录API取得真实管理员JWT，经固定/chat/agent/stream路径单独验证SQL，未伪造身份。首次04f83d1858da完整HTTP16.963秒、首反馈0.217秒（入口before_trace11.819秒）；稳定后3c8c3e63fc1e完整HTTP2.124秒、首反馈0.128秒，实际返回统计合计与月份1行。管理员API结果不能代替原业务账号页面权限验收。临时会话均正常logout，不保存/输出密码或JWT。
- 用户明确确认p4_kb_seed_doc.md为测试资料，并授权归档退出业务检索。通过正式登录与/rag/knowledge/71a1ad2d5f/lifecycle接口将policy_general文档active→deprecated，未直接UPDATE共享库，未删除原文/向量/片段。API返回ok=true，归档列表确认deprecated，原82条chunk仍存在；ai.knowledge_lifecycle_events已留actor/reason审计，后续可走既有deprecated→active恢复。
- 归档时实际发现生命周期审计查询500：can_read_row正式返回bool，而路由及旧mock误当tuple。修正测试桩匹配正式契约后复现两处失败；修复路由直接检查bool，拒绝读仍403且不返回事件、缺文档404。实际重建后审计GET返回200、存在deprecated事件。通用操作日志枚举只接受upload/reindex/delete，另报lifecycle:deprecated不支持；专用生命周期审计成功，该通用列表展示缺口未扩大修改。
- 归档后原问题父d3200ac7fcb9/子8495ab900827成功返回FAQ，完整26.635秒（before_trace9.397秒，worker_launch10.456秒，RAG4.923秒），冷路径未达标。显式问题父a612ec665715/子227ff5ef3060成功返回FAQ，总16.599秒，生成阶段9.083秒，是另一个超标样本。稳定后原问题父5659f0c8b0fe/子95810e8c8674成功5.623秒（RAG4.009秒），final_context无测试文件。引用对应e07b25dc7b；最后一次扩展来源文件名变成内部hash名，出处显示可读性仍待处理，不能据单个暖机样本宣称稳定P95达标。
- 全部代码/配置改动已从本worktree devctl.bat rebuild /y重建并返回0；最后主API/RAG/MCP/各worker健康，TOOL_INLINE_MAX_TOKENS实际2048；数据库/三端前端正常。模型未变、没有再次全量重建向量。资源一次采样app423.1MiB、RAG359.9MiB、PG414.8MiB，非峰值。
- 本轮定向测试：LCEL/重排/域分类/路由引擎/产品隔离/selector119 passed；工具输出Guard/超时策略18 passed；生命周期API/状态机26 passed。各组有2个既有pytest-timeout插件配置警告。T0 py_compile/diff通过；Docker恢复后SSE/反馈25项此前通过。本轮未跑全仓T3、并发压测或请求云端Review。
- 证据截图：D:/tmp/agent-refund-after-seed-archive.png、agent-explicit-knowledge-after-seed-archive.png、agent-refund-warm-verified.png、agent-web-results-budget-fixed.png。D:/tmp/agent-p4-seed-before-archive.json保留归档前文档元数据；SQL性能JSON仅保留业务结果与耗时，不含令牌。
- 阶段状态：工程修复与测试资料归档PASS；全目标UNVERIFIED。下一步优先定位入口冷路径、联网来源约束与整理（现有direct只透传搜索列表），再落实超过20–30秒报告走持久任务及可信产品身份隔离。不能以SSE续播或超时重投冒充后台执行续跑。原业务SQL仍需现有有权业务账号；未申请临时提权。当前不建议把全部性能目标视作通过或直接合并。

## 2026-10-27：用户要求“多测几个问题”追加10题实测

- 测试范围：8题真实业务账号页面（改写、解释、发货/支付/发票知识、无资料、联网项目来源、旅游跨产品引导）+2题正式管理员API SQL，未修改业务代码、配置、账号权限或共享业务数据，未重建容器。管理员会话正常logout，不输出/保存凭据和JWT。报告D:/tmp/agent-more-question-results-20261027.md，逐题页面JSON和SQL结果均在D:/tmp/agent-more-*。
- 6 PASS：解释2.059秒（b5b19b8ac0e2）；发货FAQ7.120秒（a0862e72ee46）；发票FAQ3.867秒（42bcea6796b0）；无资料正确拒答3.598秒（d33074473728）；管理员月订单/销售3.999秒（69870e30ba31，API首反馈0.031秒）；旅游正确引导0.449秒（31b7b75d85e0）。发货和发票均与权威chunk_store FAQ原文核对；发货虽最终正确却多走Planner。单轮性能不是P95或生产验收。
- 3 FAIL：礼貌改写2.990秒（8d483cad3b57）错误unknown→clarify；支付问答1.943秒（e6713172f163）被prefilter直通general_chat，未调RAG却声称无权限，而FAQ实际有支付方式；联网pgvector官方GitHub8.169秒（81d5a692229b）query正确且执行web.search，但返回漫画人物百科等完全无关内容。不能将这些Trace的success或SSE done算作答案通过；联网上游/解析根因尚未定位。
- 1 PARTIAL：管理员商品销量Top3 API3.075秒（566fbd7dd20e），实际返回3行且销量递减；首次SQL校验column_undefined，第二次校验/执行成功，父Trace却因历史sql.guard error仍error。首反馈0.037秒。此题答案与监控状态不一致，不能标干净PASS。
- 保存支付失败证据时额外复现：旅游会话切回支付历史后，支付标题/正文已加载，上一会话旅游卡仍在，稳定后再次观测仍存在。截图agent-more-payment-routing-failure.png和agent-more-stale-travel-card.png。同用户会话本地展示残留，未推定跨用户泄露。
- 本轮局部T2真实页面/API完成；无新增功能代码，未重复T1/全量T3/云端Review，保持未提交。总体FAIL。下一步优先支付prefilter与联网相关性/来源校验，再修改写识别、已恢复SQL顶层Trace聚合和会话卡片状态清理。
