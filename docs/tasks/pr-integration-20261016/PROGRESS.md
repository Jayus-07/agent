# PR-INTEGRATION-20261016 进度

更新时间：2026-10-16（Asia/Shanghai）
任务状态：IN_PROGRESS
主控分支：codex/pr-integration-20261016
主控 Worktree：C:\Users\wh\.codex\worktrees\pr-integration-20261016\agent
当前基线：origin/main = 04846a61f889fa561b9c2d2184cd196c93e75dd5；主控任务树已合入该基线

## G0 统一基线

| 门禁 | 状态 | 证据 |
| --- | --- | --- |
| G0-01 main/远端 | PASS（隔离任务树） | 初始 origin/main 为 45b0a7be；#73 合并后为 ed90609c；#74 合并后为 5b372540；#75 合并后当前为 04846a61。每次合并后均重新 fetch。原 main checkout 仍有用户修改，未在其上 checkout、pull 或写入。 |
| G0-02 PR 状态 | PASS | 以 GitHub 当前状态核对原 PR、Follow-up、head/base、merge 状态、Review threads、checks 和 skipped jobs。当前活动项 #52、#66、#69、#76；#73/#74/#75 已合并，#50/#60 已关闭。 |
| G0-03 Worktree 归属 | PASS | 主控、#70/#76、#50/#60 各在独立工作树；#52/#66/#69 沿用其 PR 工作树。此次未覆盖其他工作树修改；#52 正由独立施工线核查。 |
| G0-04 避免覆盖 | PASS | 根目录 main checkout 中 frontend-admin/tsconfig.json、frontend-cs/tsconfig.json、frontend/tsconfig.json 的用户修改及未跟踪 .worktree-cleanup-archive-20261010/ 均保持不动。 |
| G0-05 依赖顺序 | PASS | 快速线 #73 → #74；#70 Follow-up #76 已施工但需共享历史只读诊断后才可合并。文档 Follow-up #75 已合并。RAG #66 真实 PDF E2E 阻断；#52 可继续免费代码/测试，真实质量评测仍依赖授权价格快照。#69 独立，真实 T3 阻断。 |

## GitHub 状态快照

| PR | 当前 head / base 与状态 | Review / checks | 施工状态 |
| --- | --- | --- | --- |
| #73 | MERGED；PR HEAD d9c4d7886bd2b63685721375b2b798137df61804；Merge SHA ed90609c335753f0519add64442cf7271d75ad54 | 三条 Review 已回复并解决；当前 HEAD 的代码检查通过；真实 API Evaluation SKIPPED | MERGED |
| #72 | 原 PR 已 MERGED，Merge SHA 45b0a7be54eaf0a3b7df4676f9978b095236932f | 两条遗留 Review 已通过 #74 的证据回复并解决 | MERGED（遗留由 #74 修复） |
| #74 | MERGED；PR HEAD 179d90ecf1adf87540707e5f4832a542df02d1f8；Merge SHA 5b372540800f1b6689d371e08e79fff009200425 | Trip URL/正式行程投影；本地与 GitHub 相关检查通过；真实 Provider/RAG Evaluation 未执行且不计通过 | MERGED |
| #70 | 原 PR 已 MERGED，Merge SHA 5df8b0603355566e7fa1eb87906129c11a8211ab | 原 PR Review 0；跨会话唯一性走新 Follow-up #76 | MERGED（原 PR） |
| #76 | OPEN、Draft、CLEAN；当前 HEAD d47f43f10bd5914b0f13140d5daad18ae5b0e5b4；base main 04846a61f889fa561b9c2d2184cd196c93e75dd5 | 无 Review；当前 HEAD 的 RAG Smoke、preflight、Detect RAG changes、scan 均 PASS；真实 API Evaluation SKIPPED。共享历史重复组只读检查未完成 | BLOCKED（合并） |
| #69 | OPEN、Draft、CLEAN；HEAD c7d8f4140e87eec420da5333f74cd81f7f217ab3；base main 04846a61 | 最新 HEAD contract-check、RAG Smoke、preflight、Detect RAG changes、scan 均 PASS；真实 API Evaluation SKIPPED。main 基线定向后端事件测试 106 passed；真实 T3 未完成 | BLOCKED（真实 T3） |
| #66 | OPEN、Draft、CLEAN；HEAD 32f8406faf7c773f7d58e33a8eb90d69b00d8e49；base main 04846a61 | 最新 HEAD RAG Smoke、preflight、Detect RAG changes、scan 均 PASS；真实 API Evaluation SKIPPED。main 基线六个 RAG/PDF/Excel/coverage 定向文件 67 passed；真实 PDF IndexRun/Search 未完成 | BLOCKED（PDF E2E） |
| #52 | OPEN、Draft、CLEAN；HEAD 48675a422d6fc589ac9b46d284a1f06d90e53916，已同步 main 04846a61；14 条 Review threads 中 10 已解决、4 条 P1 未解决 | 原免费测试 160 + 重叠模型配置/API 测试 58 passed；当前 SHA preflight 53、RAG Smoke、scan、Detect RAG changes PASS；4 条 P1 Review 修复进行中，真实 API Evaluation SKIPPED_UNTRUSTED_PR | IN_PROGRESS（Review 修复；真实质量/账本仍待授权） |
| #50 | CLOSED | FE-4 已被 PR #72 覆盖；当前 main 对应三组定向前端测试 40/40 通过 | 已关闭过期 Draft |
| #60 | CLOSED | 文档中 T1 已由 PR #61 合入；独立作品集交接材料由 #75 交付 | 已关闭旧 Draft |
| #75 | MERGED；PR HEAD dba170373c4663919ab9b0e2b87b5f800d479310；Merge SHA 04846a61f889fa561b9c2d2184cd196c93e75dd5 | scan、Detect RAG changes、RAG Smoke PASS；真实 API/preflight SKIPPED；无 Review | MERGED |

原 PR branch protection 核实为 Required Checks 为空、required approvals 为 0；此次未绕过保护。Skipped job 不计为通过。

## 合并历史

- #73 普通 merge：ed90609c335753f0519add64442cf7271d75ad54。
- #74 普通 merge：5b372540800f1b6689d371e08e79fff009200425。
- #75 普通 merge：04846a61f889fa561b9c2d2184cd196c93e75dd5（当前 origin/main）。
- #72 与 #70 原始合并历史保留；后续修复均使用 Follow-up，没有改写旧历史。
- #50/#60 已关闭，未将过期 Draft 合入 main。

## 施工线状态与下一步

| 线 | 当前状态 | 下一项实际动作 |
| --- | --- | --- |
| A Provider/旅游/客服 | #73/#74/#75 MERGED；#76 代码和非付费 CI 完成，合并被共享库诊断阻断 | 待取得授权只读连接后执行 count-only 重复组查询；或由有权限的数据负责人提供结果。迁移检查前不合并 #76。 |
| B RAG | #66 BLOCKED；#52 IN_PROGRESS | #52 正在修复 4 条新增 P1 Review，后续仍需批准价格快照与只读账本权限完成真实计费/质量评测；#66 需恢复隔离 Docker Worker 并取得真实 PDF 终态。 |
| C 主助手 | #69 BLOCKED | 事件隔离已修复；等待安全可注入的隔离 Provider/BFF/APISIX/PG 与费用账本环境，仅此 PR 保持 Draft。 |
| 过期 PR | #50/#60 已关闭；#75 已合并 | 无剩余动作。 |
| main 集成 | IN_PROGRESS | MAIN-01～34 已逐项标注真实证据状态。其余 PR 当前均有独立外部门槛；恢复凭据、Docker/Worker 或数据库只读权限后继续对应施工与集成验收。 |

## 外部阻断与诊断

### #76：共享历史退款重复组只读检查

- 隔离工作树：C:\Users\wh\.codex\worktrees\pr70-refund-unique\agent；分支 codex/pr70-refund-unique；当前 HEAD d47f43f10bd5914b0f13140d5daad18ae5b0e5b4。
- 尝试只读访问应用配置目标 localhost:5432/agent_memory：psycopg2 报错 fe_sendauth: no password supplied；PostgreSQL MCP 报告 postgres 用户 SCRAM 密码认证失败。没有连接成功，也未执行共享库写操作。
- 迁移 098 在重复组非零时抛错且不改数据。合并前所需外部操作：经授权的只读凭据执行迁移注释中的 count-only 重复组查询，并确认结果；若非零，由数据负责人按案件工作流人工处置，不删除或合并案件。本地隔离 PostgreSQL 成功验证冲突预检和空键约束。
- 影响仅为 #76 的迁移合并；其他 PR 继续。

### #66：PDF Worker 真实终态

- 当前 PR HEAD 32f8406faf7c773f7d58e33a8eb90d69b00d8e49（包含修复提交 96407e7d1fb8a7a9ebf4ef6da27eb5bc7ec3dfcb，已由 `git merge-base --is-ancestor` 确认）；该提交修复 PDF 页码清洗在不确定时误删独立数字行。PR Worktree 定向回归 77 passed；当前 HEAD GitHub 的 RAG Smoke、no-paid preflight、Detect RAG changes、scan 均 success，真实 API Evaluation 为 skipped。另在 main 基线上，六个 RAG/PDF/Excel/coverage 文件 67 passed。目标上传 b96f4d86ebe3、IndexRun f7a06549-ba71-4abf-a134-3a7b3d35deb1 仍为 running，停在 table_describe；Celery active/reserved/scheduled 为空。Docker OOMKilled=false、restartCount=0，但原 Worker/Celery/Docker SIGKILL 事件日志已不可用，不能断言 OOM。
- Docker Engine named pipe 不可用；曾尝试单文件可逆改名以恢复 Model Runner，但权限拒绝，设置已恢复 SHA 77792FC4…CEE8139，未碰共享库或数据卷。
- RAG-03 PDF 最终 IndexRun、RAG-08 Search 未完成；RAG-04/09 部分或未验证；RAG-12 未验证。需恢复隔离 Docker Worker 并取得任务/Worker 事件后再施工。此前“修复已推送 HEAD 96407e7”的记录不准确：远端分支 HEAD 是 32f8406，修复提交是其祖先，故修复确实包含在 PR 当前 HEAD。

### #69：主助手真实 T3

- 当前 PR HEAD c7d8f4140e87eec420da5333f74cd81f7f217ab3，已同步 main 04846a61。已完成 Product/Tenant/User/Session/Request 事件作用域、晚到事件 fail-closed、安全元数据；当前基线定向后端回归 106 passed。最新 HEAD 的 contract-check、RAG Smoke、preflight、Detect RAG changes、scan 均 PASS；真实 API Evaluation SKIPPED。
- 隔离树缺少安全注入的 BFF API_KEY、Provider 凭据/Registry 角色和隔离 APISIX/账本链；Docker API pipe 不可用。未复制生产密钥、未触碰共享数据库、未付费调用。
- AG-09、AG-14/15 完整权限链路、AG-18/19 真实 Trace/费用、AG-20 真实 Provider 错误终态仍未验证。

## 施工证据

### #73 Provider/OCR

- 本地 37 个后端定向测试、前端 19 个测试及 TypeScript 检查通过；py_compile、定向 Ruff、diff-check、secret scan 通过。三个 Review 修复并解决。普通 merge 已进入 main；未发起额外付费 Provider 调用。

### #74 Trip 恢复与正式事实

- 创建成功后 trip_id/conversation_id 写入 URL，恢复失败保持只读；正式 Trip allowlist 投影预算 6500 字符，过滤 PII/凭据形态并将备注标记为不可信，普通问答不改 Revision。
- 前端 Vitest 23 passed、TypeScript 通过；后端 API 契约 12 passed；loopback 隔离 PostgreSQL 属主校验 1 passed；py_compile/diff-check/secret scan 通过。#72 两条遗留 Review 已回复并解决；#74 普通 merge 已完成。

### #75 客服作品集交接文档

- 核对 #60 的旧 HANDOFF 状态过期，但作品集演示、设计说明、评测和生产边界资料仍有独立价值。PR #75 新增最小交接文档/计划，修正退款政策状态及不存在的运维脚本引用。
- Markdown 相对链接核对、diff-check 通过；退款政策定向单测 32 passed；GitHub scan、Detect RAG changes、RAG Smoke PASS。真实 API/preflight 为 SKIPPED。无 Review。普通 merge SHA 04846a61。

### #76 退款申请跨会话唯一性

- 分支 codex/pr70-refund-unique，代码提交 f9fa81a566f97a639af966ecbb9d5e77f4d4401f；为跟随 #75，已将 04846a61 合入并推送，当前 HEAD d47f43f10bd5914b0f13140d5daad18ae5b0e5b4。PR #76：https://github.com/Jayus-07/agent/pull/76，OPEN、Draft、CLEAN；当前 HEAD 的 RAG Smoke、preflight、Detect RAG changes、scan 均通过，真实 API Evaluation skipped。
- 新迁移 098 只读统计历史活跃重复组，再建 tenant/user/规范化订单 ID 的活跃退款部分唯一索引；原会话级索引保留。空/缺失订单键不绕过索引。应用层拒绝空白 ID，保留预检友好消息；数据库冲突后重新查询确认 ID、订单、租户、用户、会话，跨会话同订单冲突不返成功。同一确认同 payload 复用，payload 不同冲突；结案后允许按资格重新申请；commit outcome 不明不自动新建第二申请或显示退款成功。
- Loopback 专用 PostgreSQL：迁移重复预检只读失败时记录数仍为 4；空/空白键被唯一索引阻止；不同会话同用户同订单并发结果恰为一成功一业务冲突；不同用户/租户不互阻；结案后可重申。定向真实 PG 测试 1 passed。
- Service/guard/store/idempotency/迁移/API 测试共 94 passed、20 skipped（既有条件 skip）；commit unknown 单测在该集合中；py_compile、改动测试 Ruff、diff-check、tree/diff secret scan 均通过，secret scan 0 findings。全文件 Ruff 还报告若干不在本次改动行的历史告警，未扩大修改范围。
- 共享 agent_memory 的历史重复组数量仍未验证，故 #76 只保持 Draft，不合并、不报告共享库迁移已验收。

### #52 MultiQuery 与费用

- 分支已从 origin/main 04846a61 普通合入并推送，当前 PR HEAD 48675a422d6fc589ac9b46d284a1f06d90e53916；PR Open/Draft/CLEAN，保留原 36 个文件改动。Review 线程共 14 条：10 条已解决，最新线程检查发现 4 条 P1 尚未解决，已派回施工线修复：proxy fallback 初始化异常脱敏、pricing 拒绝 per_call token 维度、拒绝同快照混币、Embedding 按 Provider 子批次分别预算/结算。
- 免费本地 MultiQuery/Proxy/预算/Embedding/Rerank/评测集 160 passed；main 同步后 #73 重叠模型配置/角色 API/健康 API 定向测试 58 passed；compileall 与 diff-check 通过。首次测试环境缺测试密钥导致 2 项 fail；使用子进程临时测试密钥/令牌重跑后通过，没有使用生产凭据。
- 当前 SHA GitHub no-paid preflight 53 passed（1 条 collection warning）、RAG Smoke required-gate、Detect RAG changes、Secret Scan 均 PASS；真实 API Evaluation 为 SKIPPED_UNTRUSTED_PR。
- 当前 SHA 真实检索质量与费用账本未验证。旧 SHA Top-1 0.7143 低于 0.80；批准价格快照及其只读访问仍不可用。本轮没有调用 Provider 或执行付费评测。Review 修复完成后仍保持 Draft，直至真实质量/账本门禁满足。

## main 集成验收状态

当前 main：04846a61f889fa561b9c2d2184cd196c93e75dd5。已合并 #73、#74、#75。MAIN-01～MAIN-34 总体尚未进行整体验收；已验证的局部链路仅按以上各 PR 记录。基础设施、全链路业务、共享数据库迁移和 RAG 真实质量未验证，不作上线就绪结论。

## 最近检查

- 原 main checkout 的用户变更和未跟踪目录保持原样。
- 控制工作树每次先核对 diff/status；不做全局 cleanup。
- 最近定向验证：#66 当前 main 基线 RAG/PDF/Excel/coverage 套件 67 passed；#69 当前 main 基线事件作用域相关后端 106 passed；主控 main 上 #73/#74 后端 API/健康合同 37 passed（使用仅测试进程令牌）。#52、#66、#69、#76 当前 HEAD 的所有非付费 required/scan/contract checks 均 PASS；相关真实 API Evaluation 均 SKIPPED，不计通过。#52 免费测试完成，等待外部批准价格快照；#66 等隔离 Worker；#69 等真实隔离 T3；#76 等共享库 count-only 只读诊断。


## main 集成验收逐项矩阵（当前证据）

状态按当前 main 04846a61 及本轮实际证据记录；PR 分支上的通过不替代 main 或真实 E2E 结果。

| ID | 检查项 | 状态 | 当前证据 / 缺口 |
| --- | --- | --- | --- |
| MAIN-01 | APISIX 正常 | BLOCKED | Docker Desktop API pipe 不可用，未运行隔离 APISIX。 |
| MAIN-02 | FastAPI 正常 | BLOCKED | 通过定向 TestClient 合同测试；真实 APISIX→FastAPI 服务链未启动。 |
| MAIN-03 | Next.js 用户端正常 | BLOCKED | 主控隔离树无 frontend/node_modules；不重复安装，未启动页面。 |
| MAIN-04 | 管理端正常 | BLOCKED | 未启动管理端；当前任务树无已装前端依赖。 |
| MAIN-05 | PostgreSQL 与 Redis 正常 | BLOCKED | 隔离 PostgreSQL 只用于已完成的独立测试；Docker/Redis 全链服务未启动。 |
| MAIN-06 | Celery Worker、Beat 和消费 | BLOCKED | Docker 服务不可用；真实 PDF Worker 任务仍无终态证据。 |
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
| MAIN-25 | 同订单跨会话不重复登记 | FAIL | 当前 main 只有会话级唯一索引；修复 PR #76 未合并。新迁移在隔离 PG 并发测试恰一成功一冲突。 |
| MAIN-26 | 申请登记不冒充资金退款成功 | NOT VERIFIED | #76 分支覆盖明确业务语义，当前 main 集成链未验收。 |
| MAIN-27 | 人工接管与失败状态正确 | NOT VERIFIED | 未运行客服 Worker/API 端到端。 |
| MAIN-28 | 多格式文件内容完整 | FAIL | main 尚未含 #66 清洗修复；当前 PDF 短数字页码歧义问题仍待 Follow-up 合并。 |
| MAIN-29 | 上传索引具有真实终态 | BLOCKED | 目标 PDF IndexRun 仍为 running、停在 table_describe；Worker 原始事件/Docker 环境不可用。 |
| MAIN-30 | Chunk / Parent 元数据正确 | NOT VERIFIED | PR #66 分支定向测试通过；main 完整索引/Search 未验收。 |
| MAIN-31 | 检索遵守授权 | BLOCKED | 无当前 main 的真实 RAG 检索服务链验收环境。 |
| MAIN-32 | 模型健康检查状态正确 | PASS | main 定向健康/API 合同套件 37 passed；测试使用临时测试内部令牌，未使用真实凭据。 |
| MAIN-33 | Token 与费用可归因 | BLOCKED | 账本/批准价格快照及隔离真实 Provider 调用不可用；#52 实际评测未执行。 |
| MAIN-34 | 已合并质量门槛有证据 | BLOCKED | 当前 RAG real API evaluation 为 SKIPPED；不计通过。 |

## 本轮 main 定向验收补充

- 后端命令：pytest backend/tests/infra/test_ocr_model_health.py backend/tests/api/test_sys_model_roles_api.py backend/tests/api/test_sys_model_health_api.py backend/tests/travel_v2/test_api_contract.py -q。
- 初次无凭据执行出现 test_requires_admin 503（1 failed、36 passed）；根因是本地未设置 AI_INTERNAL_TOKEN，fail-closed 依赖按设计拒绝且返回 503。使用进程级临时测试令牌 integration-test-only 重跑后 37 passed，环境变量仅对子进程生效，不涉及真实凭据。
- 前端在隔离主控树执行 npm test -- ...TravelChatPage.test.tsx ...TravelV2TripConversation.test.tsx 时，命令失败：vitest is not recognized；npx tsc --noEmit 无本地 TypeScript 安装。未安装依赖、未触碰原 main checkout 的 ignored node_modules，前端 post-merge 重跑保持 BLOCKED；合并前 PR #74 的 23 个前端测试证据仍可追溯。

## 2026-10-16 续工核对

- 重新执行 git fetch origin --prune。origin/main 仍为 04846a61f889fa561b9c2d2184cd196c93e75dd5；主控分支此前进度提交 96c4cb1 与远端一致且工作树干净。#52/#66/#69/#76 当前 HEAD 与上一轮记录一致，均 Open/Draft/CLEAN；全部非付费 Smoke、preflight、scan 和适用的 contract checks PASS，真实 API Evaluation 仍为 SKIPPED。
- #76 诊断重试：当前进程无 PG、POSTGRES、DATABASE 或 AGENT_MEMORY 类凭据环境变量；退款施工树仅有 .env.example。通过只读 PostgreSQL MCP 执行迁移 098 注释中的 COUNT(*) duplicate-group query，仍失败：用户 postgres 密码认证失败。没有成功连接或写入共享库，阻断条件未解除。
- #69 离线路由回归：在 HEAD c7d8f4140e87eec420da5333f74cd81f7f217ab3 运行 main_conversation_fast_path、hierarchical_router、llm_router、router_determinism、entry_mode_handoff、continuation_routing 六个测试文件，80 passed。覆盖一般问答快路径、续问、受限候选 RAG/SQL、复合意图及旅游/客服引导；不替代真实 LLM 与权限链路 T3。
- 同批探索 clarify_flow 后结果 120 passed、3 failed；在当前 main 对 3 个失败单独重跑仍是 3 failed，确认不是 #69 差异。其中旅游预过滤用例依赖 TRAVEL_ENABLED，默认关闭时失败，测试进程显式设为 true 后 1 passed。另两项 SQL 空结果测试夹具未提供当前 SQL Skill 的 is_empty 字段，却断言旧式拒答文本/未答日志；当前 SQL Skill 会提供结构化空结果标记，Reporter 对成功的 no_data 走确定性查询结果路径。保留失败证据，未改断言或扩大到无关 Reporter 行为重构。
- 当前可执行的代码、隔离测试和 GitHub 非付费门禁已完成。继续推进依赖外部状态的四项：#76 只读共享库诊断、#66 Docker Worker、#69 隔离 T3 凭据/服务、#52 批准价格快照/账本权限。外部状态改变前，不把任何一项提升为 MERGE_READY。
