# PR #55 合并后 RAG 真实上传 E2E 验收报告

日期：2026-10-14（Asia/Shanghai）
基线：合并提交 `cfa482e0bce5b1979f82304cde43bdf7d20dac5e`
验收状态：**FAIL / BLOCKED**
`RAG_UPLOAD_E2E_VERIFIED`：**false（未修改）**

## 1. 环境与方法

在独立 Worktree `C:\Users\wh\.codex\worktrees\rag-upload-postmerge-e2e-20261014-0142\agent` 的 `cfa482e0` 代码上运行隔离 Compose 项目 `rag-pr55-e2e`。API、PostgreSQL/pgvector、Redis、索引 Worker 和 rag-service 处于独立内部网络，测试 KB 为 `rag_eval_kb`；正式知识库未访问。源码只读挂载，没有修改 RAG 产品代码。

对 `C:\Users\wh\Desktop\不同文件测试文档\testset` 的 18 个文件执行真实 API 上传，并通过上传 SSE、`/rag/documents`、Chunk API、`/rag/documents/{id}/processing-runs`、`/rag/upload-failures`、Quality Record、pgvector 元数据和 `/rag/search` 对账。另执行同路径重复上传、失败同名更新、成功同名覆盖、跨部门和跨租户检索。文件目录自身不是 Git 仓库；`Not a git repo, skipping` 只表示对语料目录跳过 Git 检查，不影响文件上传测试。

索引使用缓存的本地 BGE-M3 CPU 模型，不执行付费 Provider 评测。tiktoken 词表在隔离网络不可用，代码按运行时规则使用字符估算 fallback；metadata/OCR 外部服务不可达。迁移命令返回成功，但健康检查仍报告打包迁移 `093` 与实际 applied `092` 不一致，故数据库基线不是完全无漂移环境。

## 2. 文档矩阵

18 个测试文件均收到 HTTP 200 上传受理；受理不等于成功入库。测试结束时 4 份 Active，1 份 Pending Review，12 份失败，1 份长 TXT 因 OOM/恢复仍停留在 indexing。除特别标明外，失败文档均未出现在 Active 列表。

| 文件 | Upload ID | 最终状态 | 结果与证据 |
| --- | --- | --- | --- |
| `help_center_mixed.md` | `725108738d3c` | **FAIL：Active，内容有损** | doc `52a4bacfad`，8 Chunks，Trace `874d605b7687`。搜索可命中；混合 FAQ 的 Q2/A2、Q10/A10 跨 Chunk，无法维持问答对。 |
| `faq_only.docx` | `549c7be26ff8` | **FAIL：Active，内容有损** | doc `29555be9a7`，40 Chunks，Trace `9d61b9d59e34`。搜索输出 `Q：\nA：...`，源问题文本丢失；40 个 Leaf 的父 ID 均指向不存在的 Parent。 |
| `contract_table.docx` | `ab72c2026650` | **PASS（该文档内容/检索子项）** | doc `5ca499d1e8`，5 Chunks，Trace `f8bbb5373875`。5 行、24 单元格均覆盖；表格检索返回正确金额、来源文件和 Parent 上下文。 |
| `base_v1.txt` | `7b7beeb26305` | **BLOCKED：初版待审核，之后被覆盖** | 与 `exact_copy.txt` SHA256 相同；初版生成了独立索引记录并进入审核态，后续用于同名覆盖验证。覆盖后当前 doc 为新内容版本 2，不代表原测试文件已作为 Active 验收。 |
| `exact_copy.txt` | `1a40579bcbb5` | **FAIL：重复内容未按 SHA256 合并** | 与 `base_v1.txt` SHA256 相同，仍创建独立 doc `88d34261c0` 和 8 个 Chunk，状态 `pending_review`。未成为 Active，但内容级全局去重未发生。 |
| `no_title.txt` | `101102d22d8b` | **BLOCKED：Worker OOM 后仍在恢复** | 解析出 150 个 AST Leaf、Coverage 150/150、57 个候选 Chunk；3 GiB Worker 分别在 01:10:17、01:22:19、01:29:55 UTC 三次 SIGKILL。Beat/maintenance recovery 已重投两次（`recovery_count=2`）；最后查询仍是 `RUNNING/indexing`，无终态、无 Active 发布。 |
| `no_title_oneline.txt` | `980b5092c5f3` | **FAIL：failed** | `coverage_gap(1/1)`，未 Active。 |
| `long_sop.docx` | `5fed614fb956` | **FAIL：failed** | `coverage_gap(1/6)`，未 Active。 |
| `staff_regulation_scnu.pdf` | `bbb668d3902e` | **FAIL：failed** | `coverage_gap(13/47)`，另有 6 个数字丢失的 `value_loss` 记录；未 Active。 |
| `progit_en_501p.pdf` | `48a5c62aded4` | **FAIL：failed** | 813 AST Leaf 中 5 个未覆盖；91 个候选 Chunk 全被 low-Chinese-ratio 过滤。失败而非假成功；未触发 `MAX_CHUNKS_PER_DOC=5000`。 |
| `sku.csv` | `2349c6976cf6` | **FAIL：failed** | 解析记录 22 个节点后产生 0 Chunk；501 行、2004 单元格被 Quality Record 误记为 100% 覆盖；最终 `produced 0 chunks` 阻断发布。 |
| `sku_bom.csv` | `9cd01a6dfc76` | **FAIL：failed** | 与 `sku.csv` 相同：0 Chunk、low-Chinese-ratio 过滤；无 Active 索引。 |
| `sku_gbk.csv` | `305ce3bd5688` | **FAIL：failed** | 与 `sku.csv` 相同：0 Chunk、low-Chinese-ratio 过滤；无 Active 索引。前导零 SKU / GBK 内容不能从已发布 Chunk 核实。 |
| `multi_sheet.xlsx` | `4635bcd53633` | **FAIL：failed** | `coverage_gap(1/4)`，覆盖率 75%；89 行 / 316 单元格中有 1 行、1 单元格未覆盖，质量门禁阻断发布。Sheet 顺序、公式结果及入库类型未能通过 Active 输出核验。 |
| `report.pdf` | `da90a214f7a1` | **FAIL：failed** | 解析异常：`'NoneType' object has no attribute 'strip'`；未 Active。 |
| `doc_cn.pdf` | `2e9b0e3e2612` | **FAIL：failed** | 解析异常：`'NoneType' object has no attribute 'strip'`；未 Active。 |
| `contract_gf2017_0201_cn.pdf` | `4ded4193d651` | **FAIL：failed** | 解析异常：`'NoneType' object has no attribute 'strip'`；未 Active。 |
| `scanned_partial_fail.pdf` | `ef8a61eb0cc8` | **BLOCKED：failed，OCR 环境缺依赖** | 10 页纯扫描件；配置了 `rapidocr`，但容器未安装 `rapidocr_onnxruntime`，没有可用 OCR Provider。明确失败，未假成功。 |

## 3. 上传、入库、检索与访问证据

- `help_center_mixed.md` HTTP 200，SSE `done`，文档 `52a4bacfad` Active；`/rag/search` 查询 Shopify 账单授权命中 `52a4bacfad_5`，返回正确 `source_file=help_center_mixed.md`。
- `contract_table.docx` HTTP 200，SSE 结束；`/rag/search` 查询合同服务费用命中 `5ca499d1e8_2`，表格包含 6294、28240 等值；真实 `retrieve_documents` 响应同时返回 `5ca499d1e8_0` Parent 与关联 Leaf。
- help-center 查询也返回 `52a4bacfad_0` Parent 和 Leaf，证明检索 Parent 扩展在这份文档路径有效。两份通过文档的 Parent/Leaf 关联均有效（help：7/7；合同：4/4）。
- 跨部门 `finance/employee` 和另一租户显式查询 `rag_eval_kb` 均 HTTP 403；没有返回内容。权限隔离子项 **PASS**（仅限已测主体与 KB）。
- 同路径重传相同 help Markdown（Upload `92ab4075a26b`）产生 `stage=duplicate`，SHA256 不变，原 doc 仍 `doc_version=1`、8 Chunks；API 顶层 `duplicate_of` 仍为 null，重复状态只在 IndexRun 中可见。
- 失败同名更新 Upload `a0ad357c3d27` 的 SSE 明确 `error/chunking_empty`；原 help doc 哈希、版本和 8 个 Chunk 不变，旧 Active 版本保护子项 **PASS**。
- 合法同名更新 Upload `67407a96cd14` 发布完成，原 doc ID `4c1bcfbe06` 保持不变、`doc_version=2`、状态 Active，旧 8 个 Chunk 被清理，新版本 1 Chunk；真实搜索可召回 `OVERWRITE-E2E-V2-20261014`。成功候选发布子项 **PASS**。
- 不同文件名但相同 SHA256 的 `base_v1.txt` 与 `exact_copy.txt` 产生不同记录/生成代次，故跨名 SHA256 合并去重 **FAIL**。`exact_copy` 当前 Pending Review，不作为 Active 检索成功。

## 4. Parent、Token、元数据与 Coverage

- 按 Worker 实际配置的字符估算 fallback 检查已入向量的文档：help Parent 202、Leaf 最大 499；合同 Parent 96、Leaf 最大 201；FAQ Leaf 最大 31。已验证的 Leaf 均不超过 500，Parent 均不超过 2000。tiktoken 文件下载失败，故真实 cl100k BPE 口径为 **UNVERIFIED**；长 TXT OOM、失败文档、Pending Review 及成功覆盖后的 TXT 尚未完成逐 Chunk token 检查。
- FAQ DOCX 的 40 个输出 Chunk 都是 Leaf，`parent_chunk_id=a2d44a6577c1`，但对应 Parent 不在同 doc 向量集合；FAQ Parent 引用率为 0/40，整体 Parent 100% 门禁 **FAIL**。无标题启动种子也有无效父引用，因其只用于初始化，未计入产品文件矩阵。
- CSV Quality Record 将 501 行、2004 单元格记作 covered，但生成器实际过滤所有 22 个节点并抛 `produced 0 chunks`。发布失败挡住了假成功，但 Coverage Audit 本身 **FAIL：没有用最终输出 Leaf 证明表格覆盖**。
- Excel Coverage 发现 1/4 AST Leaf 未覆盖并阻断发布，失败留痕 **PASS**；但没有 Active 索引可验证 Sheet 顺序和类型保留。
- `route_decision` 与 `quality_issues` 在 Quality Record JSON 中存在；公开 Search 向量 metadata 不包含完整 `route_decision` / `quality_issues`。成功发布 IndexRun 的 `doc_id` 字段为空，而 `/rag/documents/{id}/processing-runs` 返回的文档记录能正确关联 doc_id，索引运行记录一致性 **FAIL**。
- API Chunk 详情路由将 `len(content.encode())` 写入名为 `token_count` 的字段（UTF-8 字节数），该字段不能作为 Token 证据。实际检查采用向量 metadata 的 `chunk_tokens`/运行时 `count_tokens` fallback。
- 文档记录可见 `doc_version`，但 Active `base_v1.txt` 即使已到版本 2，`version_id` 与 `supersedes_version_id` 仍为空；版本引用元数据 **FAIL**。PDF 均失败，页码来源引用为 **UNVERIFIED**。

## 5. 门禁状态

| 门禁 | 状态 | 结论 |
| --- | --- | --- |
| 真实 API + 异步状态 | **FAIL / BLOCKED** | 18 个源文件均获 HTTP 200；多个处理失败，长 TXT 仍 indexing。 |
| 文档格式矩阵 | **FAIL** | Active 只有 4 份，其中 FAQ Active 内容有损；PDF/CSV/Excel/SOP 多个核心格式没有可检索 Active 输出。 |
| Leaf/Parent 关系 | **FAIL** | FAQ 0/40 Parent 有效。 |
| Token 边界 | **BLOCKED / UNVERIFIED** | 已测 Active 内容按字符估算符合；tiktoken BPE 未加载，若干文档未完成索引。 |
| CSV SKU 精度 | **FAIL** | 三个 CSV 均没有 Active Chunk，无法证明前导零保留。 |
| Excel 覆盖与结构 | **FAIL / BLOCKED** | Coverage 75% 阻断；Sheet 顺序/公式类型无法从 Active 索引核验。 |
| Coverage Audit 可信度 | **FAIL** | CSV 无最终输出仍报表格 100%；FAQ 丢问题仍 Active。 |
| 检索与 Parent 扩展 | **PASS（局部）** | 合同表格、混合 Markdown 的 Parent 能召回；FAQ 结果错误，完整矩阵不通过。 |
| 来源/页码引用 | **BLOCKED / UNVERIFIED** | Active Markdown/DOCX 的 source_file 正确；PDF 无 Active 文档，页码无法验证。 |
| 权限隔离 | **PASS（已测范围）** | 跨部门与跨租户均 403，无数据泄漏。 |
| 失败候选保护 | **PASS（已测范围）** | 失败同名候选保留旧 Active；成功覆盖原子发布并递增 doc_version。 |
| OCR | **BLOCKED** | 容器缺少 RapidOCR 包，无 OCR 成功验收。 |
| `MAX_CHUNKS_PER_DOC` 上限 | **UNVERIFIED** | 501 页 PDF 在超限判断之前被 Coverage/过滤阻断，未触发既有上限；未改策略。 |
| 故障恢复/失败终态 | **BLOCKED** | stale-execution 自动重投两次已实测，但 57 Chunk TXT 累计三次 SIGKILL；最后数据库查询仍为 `RUNNING/indexing`，没有成功或失败终态。 |

## 6. 发现与最小修复方向

1. **FAQ 解析与关联**：保留源问题文本；生成最终 Parent 向量或清除无效父引用；Coverage 必须核对 QA 原节点和实际输出 Leaf，避免问题丢失却 Active。
2. **表格 Coverage/过滤**：Coverage 使用过滤后的最终输出 Leaf 对账每行/单元格；SKU/数值型表格不应因低中文比例全部过滤。先修并实测 SKU 前导零/BOM/GBK，再证明索引内容。
3. **PDF 解析**：定位 `.strip()` 接收 None 的 parser 路径，最小 None-safe 处理，并为三个真实失败样本补 API 回归；OCR 依赖/Provider 必须与部署配置一致。
4. **Worker 内存和恢复**：在目标资源限额下复核无标题文件；当前三次 OOM 表明 3 GiB 隔离 Worker 不能完成此文档，自动恢复已重投两次但仍未终态。最后观察时 `lease_expires_at=2026-10-14 01:31:39 UTC`、`finished_at` 为空。修复前不得将运行中的 `indexing` 显示成成功。
5. **元数据与去重**：补齐已发布运行记录的 doc_id、版本 `version_id`/supersedes 字段；明确 SHA256 去重作用域。将 Chunk API `token_count` 改为真实 Token 口径或采用不会误导的字段名。

本轮未修改产品源码，也未创建修复 PR；发现的问题跨解析、切分、质量审计与运行资源，需按最小缺陷分别修复和回归。未发现跨租户/部门泄漏；没有把此次问题升级为未经证据支持的 P0。

最终采集后停止了 `rag-pr55-e2e` Compose 服务，未删除隔离数据库/应用数据卷；停止前长 TXT 仍在 `RUNNING/indexing`，因此该项保持 BLOCKED。

## 7. 最终判定

**不得将 `RAG_UPLOAD_E2E_VERIFIED` 置为 true。** 真实 API、数据库和检索链路已运行，但文档矩阵、FAQ 内容完整性、CSV/Excel Coverage、PDF 解析、Parent 关联、版本元数据与 Worker 资源门禁存在失败或阻断；测试过程没有修改验收旗标。PR #55 已合并至 `main` 的既有提交 `cfa482e0`，本报告只记录合并后验收，不修改已合并 PR。
