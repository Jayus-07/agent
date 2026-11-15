# 六项核心验收矩阵（最终）

日期：2026-11-16。总体结论：FAIL。验收已完成，但测试终态、生产资料、完整 Trace 历史和活动库结构仍有已证实失败；LLM 统计数据库/API授权/真实页面已通过。页码返回问题已修复，当前 214 份文件的授权 API 详情核对已完成；历史完整 Trace 仍有明确缺口。

本轮从 fetch 后的 origin/main=05176213ae1ed84cfb2cdb146060915c01a83b2d 构建并更新本机共享开发栈。PR #471 已在 2026-11-15 合并；没有新的待合并功能代码。本机桌面资料是副本；数据库核对对象为共享开发库 agent_memory 和项目数据卷，不是线上生产数据。

## 验收矩阵

| 项目 | 检查范围/分母 | 实际结果 | 证据 | 结论 | 未通过项能否自行修复 |
|---|---|---|---|---|---|
| 测试文件与终态 | 桌面 374 份测试资料；全量文件台账 470/470 | 470 份均存在、重算 SHA，SHA 与前一份本机快照 `470/470` 一致；455 个唯一 SHA。测试 374 份中 199 份按 SHA 命中 Registry，1 份只有处理运行，174 份无 Registry/处理运行。RAG 28 份：20 pass、3 soft_warning、4 baseline 意外失败；1 个预期 OCR 部分失败场景被错误路由到压力 KB，真实结果为预算失败，目标库未登记。manifest 未给 baseline 文件定义预期失败状态，按意外失败处理；预期负例按预期通过计数为 0。压力集 94/94 SHA 匹配但全部 soft_warning；维基测试资料 106/106 SHA 匹配、项目 Registry 为 0（符合仅测试用途）。268 份测试资料没有外部来源链接/日期或版本状态；本地合成/测试资料不证明生产业务事实。 | `D:/tmp/evaluation-center-acceptance-20261116/desktop-file-inventory-current.csv`；`rag-upload-28-reconciliation-current.csv`；`public-tourism-test-106-reconciliation-current.csv`；桌面 RAG `manifest.json` | **FAIL**：资料适合覆盖解析/切分场景，但已知失败和软警告未通过；文件数量/格式多不等于测试通过。 | 可修复夹具路由或解析失败，但需恢复服务、按备份和隔离 KB 单份复验；不在当前不稳定共享栈上写入。 |
| 生产候选与业务事实 | 96 份桌面生产候选，逐份 SHA/来源/版本/Registry | 96/96 纳入台账；71 份按 SHA 匹配项目登记，25 份无项目处理记录；71 份版本/内容状态栏为空，90 份来源链接/日期栏为空。42 份旧旅游候选有 41 active/soft_warning、1 pending_review；6 份公共制度副本已在 `policy_general`，均 soft_warning，缺权威来源/版本与解析切分版本；16 份停用 KB 副本仍有活动 Registry 状态。13 行生产登记表 SHA 13/13 匹配，但仅 6 份静态旅游资料在库，内容只支持公开历史/文化事实。 | `production-candidates-current.csv`；`production-register-13-reconciliation-current.csv`；`inventory-summary.json`；当前只读 DB 快照 `current-db-snapshot.json` | **FAIL**：没有足以证明平台真实业务覆盖的权威资料。生成的政策只能约束答复方式，不能代替业务事实。 | 无法由模型补写；需业务所有者提供有效原件、版本、权限与适用范围后再评估。 |
| 项目数据库与切分结构 | 当前 DB 全库、232 条活动 Registry；214 条在活动 KB、18 条在停用 KB | `travel` 1,483 chunks（383 Parent/1,100 Leaf），1,058 Leaf 无父指针、358 Parent 无子项；47 份均缺版本/生效时间，41/47 缺解析器/切分策略版本。`rag_100_docs` 94 份全 soft_warning，371 Leaf 无父指针、249 Parent 无子项，且缺解析/切分/版本元数据。`rag_eval_kb` 23 份、2,551 chunk 行，91 Leaf 父指针为空、4 Parent 无子项。`policy_general` 25 份，325 Leaf 父指针为空、92 Parent 无子项，23/25 缺解析/切分版本，25/25 缺版本/生效时间。样本 `travel_eval_kb` 131 块（23P/108L）父子结构通过；隔离试点 8 块（4P/4L）父子指针齐全，原文非空覆盖缺口仅 6 个空白符。 | `active-doc-structure-current.csv`；`chunk-structure-by-kb-current.csv`；`chunk-structure-anomalies-current.csv`；`pilot-trace-db-current.json`；`current-db-snapshot.json` | **FAIL**：样本结构通过不能抵消活动库结构/版本元数据缺口。已更正旧记录中 `travel=1,614` 的混加错误；131 块属于 `travel_eval_kb`，不是 `travel`。 | 当前不能安全修库：Redis 重启中、BGE 不可达；需先备份和隔离/版本化 KB 单文件验证。不能批量改 `travel`。 |
| 向量/BM25 写入与实际检索 | 当前已有索引；只读检查 1 个测试 KB 检索问题 | 数据库现有向量数为 60,763；RAG 服务 /readyz=200 ready。对 travel_eval_kb 以《鼓浪屿位于哪里？》调用 /retrieve_docs（top_k=5，candidate_k=20，customer/public），返回 index_status=ok 和 6 个片段，均归到 doc_id dc9db68a37、厦门-城市-厦门.md，内容与问题相关。这证明当前混合检索路径有真实召回；不是生产业务事实，也不是全库准确率。没有把向量腿和 BM25 腿贡献拆开计分。 | D:/tmp/evaluation-center-deploy-backup-20261116/rag-retrieval-smoke-20261116.txt；RAG 服务 /openapi.json；PostgreSQL 只读快照 | PASS（限定为测试 KB 的 1 条真实检索烟测）；全库覆盖和检索准确率不在此结论范围。 | 更大范围检索需等 Redis、BGE 与宿主资源稳定后在隔离 KB 继续；当前不做批量索引。 |
| 管理端 Trace 当前页入口与详情 | 当前登录管理端 214 个文件入口；所有页面 API 分页 + 3 个真实 UI 路径 | 授权 API 枚举 214/214 文件、214 个唯一 doc_id。18 个 Trace API 详情成功且 doc_id 相符；另外 189 个当前运行详情和 7 个同 SHA 历史运行详情均有阶段、status=success。页 2 实测 `leave_policy_hr.md` 的 run、doc_id、SHA、11 阶段，Trace 404 后 UI 明确标为处理运行记录（非 Trace）；页 9 实测完整 Trace 文件名与 Registry 一致；页 10 实测 SHA 历史回退 `faq-exchange-repair.md`，11 阶段。第 2 页显式返回与浏览器后退都恢复 `doc_page=2`；第 9 页返回恢复 `doc_page=9`。 | D:/tmp/evaluation-center-deploy-backup-20261116/trace-runtime-acceptance-fix-20261116.txt；`http://127.0.0.1:3200/knowledge/workbench?tab=documents&doc_page=2`、`doc_page=9`、`doc_page=10`；GET `/api/rag/documents?page=1..11&page_size=20`、Trace 和 processing-runs 详情 API | **PASS（入口/可用详情全量 214/214；只有 18 条可读完整 Trace，运行详情单独统计）**。 | 分页返回问题已由 URL 保存页码修复并完成定向测试、生产构建、真实页面往返复验。 |
| Trace 历史记录完整性 | 当前 214 个活动文件的 Trace ID 与处理运行/历史 API | 192 个文件带 `last_trace_id`：18 个 Trace API HTTP 200，174 个 HTTP 404（已过期/不可取回）；这些 174 项当前可走同文件处理运行详情，但不等于恢复完整 Trace。另有 22 个文件没有列表级 Trace ID。全量当前文件最终都有可用详情，但只有 18/214 可读为完整 Trace。4 个可读 Trace 的 `question` 与文件名字段不同；管理端从 Registry 列表参数显示真实文件名，已在页 9 实机验证。之前台账列的 6 个缺详情文件现在在历史处理运行 API 找到同 SHA、有 10/11 阶段的记录，`faq-exchange-repair.md` 已实机验证；这不回填或恢复历史 Trace。 | D:/tmp/evaluation-center-deploy-backup-20261116/trace-runtime-acceptance-fix-20261116.txt；GET `/api/observability/traces/{trace_id}`、`/api/rag/documents/{doc_id}/processing-runs`、`/processing-runs/{run_id}`；旧快照 D:/tmp/evaluation-center-acceptance-20261116/trace-entry-ledger-current-214.csv | **FAIL（完整 Trace 只读到 18/192 个登记 Trace ID；174 个 Trace API 404；缺失事件不能补造）**。文件处理详情可用不抵消完整 Trace 历史缺口。 | 可修复未来新运行的留存；已过期历史 Trace 无法自行恢复。需要保留策略/Trace 持久化改进和后续真实运行验证。 |
| 当前部署全量文件入口/详情复验 | 当前 214 个活动文件；API 11 页 + 每文件关联详情 | 214/214 名称、doc_id、SHA 和 Trace/run 候选已枚举；18 条 Trace 详情、189 条当前运行详情、7 条同 SHA 历史运行详情，0 条未解决。初次并发扫描中 2 条临时归为不可用，经单独 GET 重查为 success、11 阶段，已更正为当前运行详情。浏览器对页 2、9、10 进行入口、详情与返回路径验证。 | D:/tmp/evaluation-center-deploy-backup-20261116/trace-runtime-acceptance-fix-20261116.txt；本机共享管理端只读 GET API 与实际浏览器 | **PASS（当前全量入口/详情状态分类已完成）**；不代表 214 条都存在完整 Trace。 | 页码状态已修复。174 个过期 Trace 属上方历史完整性 FAIL，不能用处理运行记录冒充 Trace。 |
| 可修复的管理端 Trace 分页返回问题 | 文档列表页码、Trace/处理详情返回、两个定向 UI 测试和本机管理端构建 | 修复列表页码写入 URL，进入 Trace 或处理详情时带回列表页签和页码；详情显式返回及浏览器后退均回到来源页。定向测试 2 文件/12 用例通过；管理端生产构建成功并仅重建 frontend-admin。真实页面验证第 2、9 页返回页码保留，第 10 页 SHA 历史运行详情可见。 | `frontend-admin/src/components/workbench/knowledge/DocumentsPanel.tsx`、`frontend-admin/src/app/knowledge/operations/traces/[id]/page.tsx`；`D:/tmp/evaluation-center-deploy-backup-20261116/trace-runtime-acceptance-fix-20261116.txt`；管理端 image `sha256:37469c534c563d2a567b5055c932a4c2e994771830efe363f825789afa88fa3a` | **PASS（本机共享开发栈；不代表线上部署）**。 | 已修复并实机复验；修复仅在当前任务工作树构建的本机管理端。 |
| LLM 兜底持续统计、授权、页面 | 30 天共享开发 DB 聚合、当前 OpenAPI/匿名授权、真实登录管理页 | DB：候选/调用/采用/Provider 失败/边界拒绝/未采用/跳过/输出块=5/5/3/2/0/2/0/21。全部 audience=test；类型为 TXT / no_recognized_structure / RecursiveChunkStrategy / rule v2 / parser v1。当前登录页面与 DB 汇总完全一致；表格可见 Provider 失败 2、边界拒绝 0、阈值 5，结论为测试观察（不触发升级）。OpenAPI 200 有 /rag/chunking/llm-fallback-stats；匿名直接请求 401，证明需要授权。当前页能够成功加载登录态统计。 | D:/tmp/evaluation-center-deploy-backup-20261116/live-runtime-evidence-authenticated-20261116.json；admin-workbench-and-trace-live-20261116.txt；llm-fallback-events-current.csv；GET /openapi.json 与匿名 API 401 | PASS（本机共享开发运行态、30 天聚合与测试库升级隔离通过；不等于生产数据统计）。 | 已在本机共享管理端部署并验收。 |
| PR #471 合并、CI 与本机部署 | PR #471 GitHub 合并记录/Checks；最新 origin/main；本机共享栈 | PR #471 于 2026-11-15T15:49:38Z 已合并，merge commit 08413877d0b44fa3701fe06066261f59871df380；7 项成功、1 项真实 API RAG Smoke 跳过。当前 HEAD=origin/main=05176213ae1ed84cfb2cdb146060915c01a83b2d。app /health=200、commit 精确匹配、dirty=false；管理端容器 running/healthy，登录后的页面和统计/Trace 实际可见。只部署到本机共享开发栈；没有部署线上，也没有修改线上生产库。 | PR #471 Checks；D:/tmp/evaluation-center-deploy-backup-20261116/live-runtime-evidence-authenticated-20261116.json；当前 http://127.0.0.1:8000/health；当前管理端页面 | PASS（PR 已合并；本机共享开发部署通过，不声称线上部署）。 | 不适用。 |

## 资料缺件与逐份归属

完整 470 份文件台账含相对路径、类型、SHA-256、用途/来源、项目匹配和终态；生产候选另有 96 行台账。以下是阻止生产业务覆盖验收的具体缺件：

- **合同/协议候选 8 份**：`合星_用户协议.pdf`、`京穗公司与商品推广方、商品生产企业三方采购协议.docx`、`林稳_用户协议.pdf`、`年货节商品展销协议书（2026.01.29）docx.docx`、`年货节商品展销协议书（2026.01.29）docx(1).docx`、`年年糕_用户协议.pdf`、`融宝支付服务协议-通用线下版.pdf`、`用户协议(1) - 副本.pdf`。均缺适用业务/签署状态/现行版本/有效期/知识库权限确认；未导入。`来源清单.csv` 和 `资料说明.md` 仍写 5 份；`合星_用户协议.pdf`、`林稳_用户协议.pdf`、`年年糕_用户协议.pdf` 未登记。
- **企业项目案例 3 份**：`深入开展“数字大田”项目示范建设_加水印.pdf`、`深入开展“数字大田”项目示范建设.docx`、`深入开展“数字大田”项目示范建设.pdf`。缺业务归属和原件权威确认，项目库没有同 SHA 记录。
- **公共制度副本 6 份**：`差旅费报销管理办法2026修订版.txt`、`新品上架运营指引.md`、`员工入职办理须知.docx`、`general_office_rules.md`、`general_security.pdf`、`secret_subsidy_policy.pdf`。虽然数据库可见，但来源权威、现行版本、生效日期、权限和原始解析版本未核；不能据此断言是真实正式制度。
- **员工手册**：`员工手册_2026版.pdf` 有本机原件，但当前没有可用的 active/pass 项目记录，权限也待核；不作为已覆盖的人事制度。
- **停用内部 KB 副本 16 份**：本机 2 份 `biz_product`、5 份 `policy_finance`、9 份 `policy_hr` 候选有记录，但对应 KB 已停用且 Registry 仍 active；不能当成当前可用生产资料。
- **42 份旧旅游候选**：有文件级 SHA 和项目终态，但业务来源、服务范围、版本/有效期不完整；静态旅游文案只支持公开历史文化事实，不能证明实际运营、服务商、产品价格、库存或可预订性。
- **尚未提供的业务原件/事实**：现行 HR/财务制度与有效期；正式合同和适用范围；供应商/商品价格与条款；客服 SLA、退款退货、配送/转接等承诺；旅游服务商、线路/产品、价格、开放时间和实时供应状态；生产 SQL Schema、指标定义、权限矩阵和测试账号。需由业务所有者提供并确认来源，不能生成代替。

## 切分、索引和错误分类补充

- **测试 PDF 失败阶段复核**：只读处理运行 API 显示 `UF-09-01` 在 OCR 覆盖硬门禁失败（`ocr_coverage_partial`）；`UF-13-01` 为 `coverage_gap,ocr_coverage_partial`；`UF-14-01` 为解析页未覆盖/源文本审计缺口/OCR 部分失败/PDF 页覆盖不足；`UF-14-02` 为解析页未覆盖/OCR 部分失败/PDF 页覆盖不足。四份 manifest 都没有声明预期失败，保持意外失败。硬门禁阻止不完整内容被发布；本轮不绕过质量门禁、不改桌面原件，等模型与 Redis 健康后在隔离 KB 按单份资料复验。
- **预期 OCR 部分失败样本误路由**：`scanned_partial_fail.pdf` 的目标 `rag_eval_kb` 没有 Registry；唯一运行/索引失败记录位于 `rag_upload_pressure_20261113`，错误为 request token budget exceeded。不能据此验收其 OCR 部分失败预期，也不把压力 KB 里的历史失败移到目标 KB。
- **结构通过**：`travel_eval_kb` 的 131 块、Parent/Leaf 指针、131 条 Chroma 向量及质量记录通过；隔离试点 8 块为 4 Parent/4 Leaf，4 个 Leaf 与原文有完整非空覆盖，向量/BM25 阶段各记录 8 次写入。
- **结构不通过**：旧 `travel`、`rag_100_docs`、`rag_eval_kb`、`policy_general` 的父子关联和版本元数据缺口按矩阵中的全量 SQL 数量判定；没有批量修复。
- **非故障的计数差异**：`contract_table.docx` 质量记录 5 块，但 Parent 标记 `vectorize=false`，实际应索引的 4 个 Leaf 与向量/BM25 写入均为 4；这是显式策略。
- **小样本实际召回**：本轮对 travel_eval_kb 的测试问题调用 RAG 服务真实 /retrieve_docs，返回 6 个相关片段，index_status=ok；只证明测试 KB 的混合检索路径可用。未分别归因向量/BM25 的召回分数，不外推到生产或全库。
- **当前运行限制**：app /health=ok、build commit=05176213ae1ed84cfb2cdb146060915c01a83b2d、dirty=false；管理端容器健康，RAG 服务 /readyz=ready。共享 Redis 仍 restarting/unhealthy，app 报 Redis unknown/degraded；本地 BGE 127.0.0.1:8088/healthz 连接拒绝。一个只读测试 KB 检索已实际成功；不在该状态下上传、索引、重建或批量修库。

## Trace 与运行页面证据边界

当前管理端真实登录页为 http://127.0.0.1:3200/knowledge/workbench，显示 214 篇、11 页。已修复页码返回后重挂载回第 1 页的问题，并实测第 2 页进入处理运行详情后，显式返回与浏览器后退都回到第 2 页；第 9 页完整 Trace 与第 10 页 SHA 历史回退也完成实机核对。当前 214 条由授权 API 全量枚举并查找对应 Trace/处理运行详情；逐项计数见矩阵和 `D:/tmp/evaluation-center-deploy-backup-20261116/trace-runtime-acceptance-fix-20261116.txt`。API 404 的 174 条登记 Trace 明确归为历史 Trace 缺口，不能以运行详情冒称完整 Trace。

2026-11-16 当前 API 全量记录为：18 个完整 Trace；196 个有阶段同文件处理运行（189 个当前 run + 7 个同 SHA 历史 run）；0 个当前文件入口无详情。192 个 `last_trace_id` 中有 174 个 Trace API 404，因此完整 Trace 历史仍为 FAIL。之前台账中六个“详情不可用”文件已找到同 SHA 阶段记录。4 个 Trace `question` 与 Registry 文件名不一致的文件为：杭州-西湖与西溪主题线路参考.md、杭州-世界遗产主题.md、厦门-鼓浪屿建筑风格.md、福州-城市文化主题.md；第 9 页已实机确认 H1 用真实文件名，底层历史 Trace question 不被改写。

修复前分页重置已解决。当前管理端页面、API 全量计数及三条真实浏览器路径保存在 `D:/tmp/evaluation-center-deploy-backup-20261116/trace-runtime-acceptance-fix-20261116.txt`。历史 Trace 404 仍按缺口记账，不声称能恢复，也不把非 Trace 运行详情改称完整 Trace。

## 本轮操作与停止条件

- PR #471 已在 GitHub 合并。本机从 fetch 后最新 origin/main=05176213ae1ed84cfb2cdb146060915c01a83b2d 生成干净源码构建上下文，只更新已有共享 agent 开发栈的 app 与 frontend-admin；未新起完整栈、未提交任务记录、未再开 PR，也未部署线上。app image sha256:9320101d4f5d83175ae42337a094abbc98de8425ee7283f5edc68c85c613517c；admin image sha256:0cfaccc64b453281a1482528b51740a5e541a774043a9dbe2f6dc1f00a03ba4e。
- 更新 app 前备份本机共享开发库：D:/tmp/evaluation-center-deploy-backup-20261116/agent_memory-before-deploy.dump，355.51 MiB，SHA-256 44F1A64C98DB93D747B6226233870B07DC9A52602791056CC6432F50FF44CF9A，pg_restore -l 1,378 行。app 启动完成 checkpoint 初始化和 prompt defaults seed，自动 LLM warm-up 成功输出 2 个字符；这是明确记录的启动写入。没有上传知识文件、生成 chunk、写向量/BM25、重建索引或改线上数据库。部署前后 Registry 数一致。
- 部署后真实状态：app /health=ok、exact commit、dirty=false；admin 容器 healthy；OpenAPI=200 且含统计路由。真实登录 UI 显示统计值与 DB 对齐。匿名 API=401 是授权保护的预期。数据库只读查询确认：21 KB、2,590 Registry、6,077 chunk_store、60,763 rag_vectors、12,846 processing_runs、108,200 processing_steps；30 天兜底指标详见统计行。
- 实际检索只针对测试 KB 的一条公开旅游问题，POST http://127.0.0.1:8090/retrieve_docs 成功，index_status=ok、6 chunks、同一测试文档。未访问生产事实库做额外探索，未扩展成 Ragas。
- 最终环境：宿主可用内存 2.13 GiB；共享 agent-redis-1 restarting/unhealthy，app 将 Redis 标为 unknown/degraded；本地 BGE 健康端口不可连接，虽 RAG service /readyz=ready 且样本检索成功，仍不启动上传/索引/重建，也不执行批量旧库修复。
- 验收已收口，整体 FAIL（验收完成不等于全部 PASS）。测试集、生产事实、旧库结构、历史 Trace 缺件按 FAIL；全量 214 Trace 当前页逐一复验按 BLOCKED；统计真实 UI/API/DB 和单条测试 KB 检索按各自范围 PASS。剩余条件已在矩阵注明具体名称、影响及所需条件。
