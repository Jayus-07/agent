# B1 施工进度

- 状态：B1 代码、报告与真实验收收口完成；PR #66 保持 Draft；CI 已通过，等待 Review；不启动 B2/B3/C。
- Worktree：`C:\Users\wh\.codex\worktrees\rag-upload-b1-20261014\agent`
- 分支：`codex/rag-upload-b1-20261014`
- 基线：PR #58 merge `1acf6a4b34fdae4ba257a2e9bfcb1a24ee621d01`；施工起点为最新 main `bf3cddf7ac7329f576f036cb1a9717a8f96dfd27`。
- PR：[ #66 Draft](https://github.com/Jayus-07/agent/pull/66)。原始修复提交 `b9b93405465f9fb6566c25b06d9ef49ef53e0c48`；进度文档后续有独立记录提交，最终 HEAD 以分支最新提交为准。
- “Not a git repo, skipping” 是测试语料目录的提示；已核实修复目录是有效 Worktree，分支和 Git 状态正常。

## 根因与改动

- CSV 表格行曾被普通正文的中文比例、长度与相似度规则过滤，造成 0 有效 Leaf。对既有表格 Chunk 类型做局部类型感知过滤，保留 SKU、数字、原始行及普通正文过滤规则。
- PDF span 文本/表格单元格为 None 时旧逻辑异常；补齐 None 安全处理。另修正法律编号清理和长正文既有切分的 Coverage 重组。
- Excel 多级表头曾仅用于列名，未进入最终 Leaf；公式缓存缺失时丢失公式。现在输出真实表头 Leaf，缓存缺失则保留公式表达式。
- Coverage 只依赖实际输出 Leaf；补齐拆分组连续内容和表格行/单元格级审计，覆盖重复行缺失、单行 Sheet、超长表格行等回归。
- 没有新增切分策略、第二套质量门禁或异步恢复机制。

## 验证结果

- T0：修改 Python 文件 `py_compile` PASS；`git diff --check` PASS（仅 CRLF/LF 规范化提示）。
- T1/T2：六个定向测试文件合计 65 passed：B1 内容完整性、Chunk 类型契约、PR55 Coverage/QualityGate、Excel Parser、PDF Parser、Legal Chunking。
- T3 CSV：`sku.csv`、`sku_bom.csv`、`sku_gbk.csv` 已真实 API→Worker→PGVector→Search 完成，详情见 [REPORT.md](REPORT.md)，三者 PASS。
- T3 Excel：`multi_sheet.xlsx` 同内容容器副本真实上传，Worker 最终代码重索引成功，数据库 Parent/Coverage/Quality Record 对账及三类 Search 验证通过；见 [REPORT.md](REPORT.md)。
- T3 PDF：真实 API 接收且 Parser 成功；Worker 子进程 SIGKILL 后无最终 IndexRun/终态/Search，记 BLOCKED，未将其归因到 Parser。需后续 B2 查明 Worker 终止原因后补齐。
- 直接 PDF 解析不等于真实 E2E。大 PDF Coverage 的抽样结果保留 `sampled=true` / `coverage_rate=null`。
- 精确 cl100k BPE Token 统计 UNVERIFIED，因当前网络无法取得 tiktoken 词表；当前运行时使用项目已有字符估算回退。
- RAG 全局状态继续为 `RAG_UPLOAD_E2E_VERIFIED=false`。

## PR / 最终门禁

- PR [#66 Draft](https://github.com/Jayus-07/agent/pull/66)；代码提交 `b9b93405465f9fb6566c25b06d9ef49ef53e0c48`，报告/进度记录另有文档提交。
- 最新核验的 PR HEAD `ee26a386b4120331224c8d1ebafb5865582b704e`：RAG Smoke preflight、免费定向回归、Detect RAG changes、Secret Scan 均 PASS；RAG Smoke 的真实 API 评测 job 按 workflow 跳过。
- PR Reviews：0；未解决 Review Threads：0。
- PDF 上传仍无最终索引/检索证据，故保持 Draft，不建议合并。
- 本轮到此停止，不启动 B2/B3/C，不跑付费 Provider，不修改正式数据库或全局验收状态。
## 2026-10-18 合并 main（含 #90）与 B1 PDF SIGKILL 根因证据

- 合并最新 main `fd1722c`（#90）。冲突仅 `cleaner.py` / `filter.py`：按「#90 实现为唯一来源、删除 #66 重复实现」处理——cleaner 保留 #90 的节点级清洗/删除证据/清洗对账，采纳 #66「不猜裸数字为页码」与只删带标签页脚；filter 以 #90 版为基线，补回 #66 的 `split_group_id` 豁免分支。
- 同步过期断言：短源 Leaf 现在由过滤器直接判 `clean`（不再走过滤后回捞并改写 `filter_status`）；裸数字行不再被清洗删除（审计仍可按形态归类）。合并后定向集合 **211 passed / 1 skipped**，16/16 fixture 离线完整性 **TOTAL 16 FAIL 0**。
- **PDF Worker SIGKILL 根因（实测）**：运行中的 `agent-rag-index-worker-1` 容器 `HostConfig.Memory=805306368`（768 MiB，compose 声明 3g、concurrency 默认 2）。本地对 500 页文本 PDF 执行 `parse_and_chunk_full` 峰值 RSS **836.2 MB**（75.7s，9,501 AST 节点，9,500 chunk → 超过 `MAX_CHUNKS_PER_DOC=6000` 被截断）。单文档解析峰值已高于运行容器上限 → cgroup OOM 以 SIGKILL 终止 Celery 子进程（父进程存活、容器 `OOMKilled=false`），与现场「子进程 SIGKILL、无最终 IndexRun/终态/Search」一致；并发 2 时对 3g 上限也接近临界。
- 解除 B1 阻断所需（本轮未执行，避免在未验证情况下改部署资源）：① 将 rag-index-worker 限额提升到 ≥ 单文档峰值的 2 倍（或把 concurrency 降到 1）；② 用 #66 HEAD 重建并启动 stack；③ 上传真实 501 页 PDF，取回 IndexRun/Registry/终态/Search 证据。当前 #66 仍保持 Draft，不宣称 PDF E2E 通过。
- 说明：超大 PDF 产出 chunk 超过上限属既有 fail-closed（文档 failed），与本次 OOM 是两类问题。
## 2026-10-18 更正：Worker SIGKILL 的 OOM 归因未被复现

- 复核运行配置：实际生效内存上限 768MiB（`memory.max=805306368`，来自本机 4C8G 覆盖层，非 compose 声明的 3g），`RAG_INDEX_WORKER_CONCURRENCY=1`；cgroup `memory.events` 当前 `oom_kill=0`。
- **反证**：在容器内（同一镜像 + 挂载 #66 代码）以 `--memory=768m` 解析同一份 500 页 PDF，峰值 RSS 仅 **280.5MB**，解析成功（124.8s，9500 chunk → 超过 `MAX_CHUNKS_PER_DOC=6000` 截断）。此前 Windows 宿主测得 836.2MB，不能与容器 cgroup 峰值直接比较。
- 结论修正：**768MiB 上限导致 OOM SIGKILL 的假设不被现有证据支持**，Worker SIGKILL 根因仍为 UNVERIFIED。可能的下一层原因：Docker Desktop VM 级内存压力、任务执行到 embedding/元数据阶段的内存峰值（本实验只覆盖 parse+chunk）、或容器被 `docker stop`/重建时的 SIGKILL；需要原始 worker 日志或完整 stack 复现才能定论。
- 保留 `部署/prod-4c8g/docker-compose.rag-t2.yml`（并发 1 + 2g）作为隔离测试余量与可复现配置，但在根因确认前**不宣称已修复**；#66 继续保持 Draft。
- 另记录：当前 `backend/config/rag.py` 的 `MAX_CHUNKS_PER_DOC` 默认值为 **6000**（非 5000），超限文档仍按既有 fail-closed 拒绝；B1 的“超限 PDF 受控失败”用例需按 6000 门槛构造。
## 2026-10-18 隔离 Worker PDF E2E PASS（B1 收口）

- 隔离环境：自建 PG（172.17.0.2，默认库 agent_memory/agent_business 由测试库克隆）+ Redis（172.17.0.3），worker 容器 2GiB cgroup、concurrency=1、挂载 #66 后端代码；不读写正式库/卷。
- canonical BM25 初始化：先 `refresh_registry()` 加载 DB 凭据 → 用 `PgVectorKnowledgeStore`（1024 维真实 embedding）写入 seed chunk → `RAGPipeline(mode='index', auto_sync=False)` 由 canonical 向量集合生成 `bm25/{corpus,docs,index}.pkl + meta.json + PUBLISHED.json`；worker 以 runtime 只读模式成功加载并通过快照校验（未关校验、未复制正式 BM25）。
- **有效 PDF（tech_技术手册.pdf，真实 Celery 任务）**：`upload_id=e2e-pdf-valid-7`，task `528a258b`，43.7s 成功；Indexer 质量记录 nodes=39/chunks=38/anomalies=0；BM25 快照发布 38 chunks；Publish `gen=7f69439fd1af`；Registry `f5a912bf87 / active / 38 chunks`；PGVector 20 parent + 20 leaf；Search「Git Flow 分支规范」命中该文档 leaf+parent。
- **超限 PDF（large_500p.pdf）**：真实切分 9500 chunks > `MAX_CHUNKS_PER_DOC=6000` → 截断 → 付费前预检 `chunks_truncated` 硬异常；终态 `validation_error`、`retry_count=0/3`（无无界重试）；Registry 保持原 2 个 Active 文档不变；cgroup `oom_kill=0`、峰值 544MB < 2GiB；无 SIGKILL。
- **失败后恢复**：超限失败后立即索引 returns_退货政策.pdf，task `de70b7ef` 16.7s 成功、Publish `gen=fa678034f84d`、Registry 新增 active `af22a9bf1c`；Celery `inspect active` 为空（无僵尸任务）。
- 结论：#66 Batch B1 的「Worker 正常执行 + 有效 PDF 终态 + 超限受控失败 + 失败后恢复 + 旧 Active 保护」验收通过；未复现 SIGKILL，未改 Parser/上限/门禁；BM25 一致性校验保持开启。
