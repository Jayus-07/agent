# 进度记录

## 当前状态

IMPLEMENTED（第二轮）— 待用户审阅；未提交、未合并。

## 起点证据

- 源分支：`main`，启动时工作树干净。
- 任务 Worktree：`C:\Users\wh\.codex\worktrees\rag-enterprise-structure\agent`
- 任务分支：`codex/rag-enterprise-structure`
- 起始提交：`9a1dbe299e342cb51503ad98bd8f121d85e92cde`。
- 当前 HEAD 仍为起始提交；功能变更保持未提交。

## 已确认现状

- `backend/rag/preprocessing/pipeline.py` 已有 Parser → Cleaner → StructureAnalyzer → ChunkStrategyRouter → ChunkStrategy。
- 现有解析器：PDF、DOCX、XLSX、CSV、Markdown、TXT。
- 已有 FAQ/法律/表格/结构/递归策略，结构决策有版本记录。
- 已有质量审计、租户权限过滤及 candidate generation 发布能力。
- 本次实施前的主要缺口为 HTML/PPTX/JSON/XML、稳定溯源、嵌套混合结构路由、分层索引元数据和检索文本边界；已按下方记录补齐。

## 已实施

- AST 增加稳定节点 ID、父节点/章节 ID、章节树、字符跨度、格式与 Parser 版本；既有 PDF 页/bbox 保留，DOCX、CSV、XLSX 增加定位信息。
- 注册 HTML/HTM、PPTX、JSON、XML 解析器；上传 MIME 派生白名单、文本伪装检测及 OOXML ZIP 魔数校验同步覆盖新格式。
- 新解析器保留 DOM/XPath/JSON 路径/幻灯片形状定位；支持表格与图表缓存、讲者备注；未解析图表或无替代文本图片写质量问题，XML 拒绝 DTD/ENTITY，超限结构标记为不完整。
- 混合内容单元递归到嵌套章节路由；FAQ Chunk 增加问题、FAQ ID 和章节路径；切分策略版本升级为 `ContentUnitDispatchStrategy:v2`。
- Chunk 增加章节 ID、来源节点/span/定位 JSON、格式/解析/切分版本、内容 Hash、精确标识符、质量标记；Document Index metadata 补 Parser、策略、Embedding 版本、候选代次和索引时间。
- 分离引用正文、向量上下文文本、BM25 搜索文本方法；实体只在 Chunk 原文逐字出现时绑定。
- Quality Record schema 升为 1.3，保存章节树、稳定节点身份、来源定位和索引元数据；补充 RAG 现行文档和定向测试。
- RapidOCR 区域平均置信度与文档最低值进入 PDF AST/质量记录；低于 0.7 标记低置信度，云端无分数时标记不可用，不伪造分数。
- 上传弹窗文件选择器增加后端已注册格式；修复 `106_approval_tenant_isolation.sql` 在 `MIGRATION_TARGETS` 中重复登记到 memory、覆盖 business 的既有错误，并加入回归测试，确保重建迁移能在权威库通过。

## 第二轮修复（2026-10-22）

- 表格身份收口：表格 Chunk 增加 `table_node_id`，`stamp_chunk_structure` 按具体表格节点回填 DOM/XPath/工作表定位；同一章节多表不再回退到第一张表，缺身份时宁可留空也不指错。
- 章节身份收口：合同条款、JSON 记录等隐性层级按完整标题路径派生稳定 `section_id/parent_section_id`，`section_type` 覆盖 section/clause/slide/record/element；派生章节回写章节树，虚拟 Parent 使用组内公共祖先，不再挂在首个条款下。
- FAQ 去重：`exclude_qa` 同时排除合并后的 `qa_pair`，混合文档不再重复产出同一 Q+A。
- 文本边界与类型：普通 Leaf 补 `chunk_type`（paragraph/list/code/step/legal_clause 等）；Markdown 增加 fenced code 与 pipe table；PPTX 无标题页生成 slide section；JSON 无 id 记录用“记录 N”并标记 heading_source；HTML 补 class/id/role 广告位过滤；XML 表格补 columns。
- 索引治理：Registry 新增 `title/language/source_format/parser_version/chunk_policy_version/embedding_version/index_generation/indexed_at` 列（schema + 惰性迁移 + 010 SQL）；质量记录改由 doc_meta 直接写入索引运行元数据，修复原先在 chunk 索引字段写入前构建导致为空的问题；indexer 增加确定性中英文语言检测。
- 版本：`PIPELINE_VERSION=4`，受影响解析器 `PARSER_VERSION=v2`，`StructureChunkStrategy` 升 v3；质量记录 schema 升 1.3。
- 质量门禁：新增 Leaf 引用定位可用性审计（缺页码/跨度/来源定位记 warn，可观测不误阻断）。

## 验证记录

- `python -m compileall -q backend/rag/preprocessing backend/rag/indexing backend/app/api/routes/rag_upload.py scripts/init_db.py`：通过。
- 结构、解析、上传、OCR、质量门禁、切分兼容定向组：146 passed，2 deselected；未改动的 PDF 覆盖 Fixture 缺少页码映射、PostgreSQL 权限集成测试需要数据库凭据及隔离条件，因此未纳入该组。
- 索引管线、Fixture metadata、BM25、候选发布、Chunk metadata 与 lineage 定向组：30 passed，2 deselected；跳过需要 PostgreSQL 的索引集成用例，以及现有问题生成用例的返回值解包不匹配（与本次 Diff 无关）。
- 迁移登记回归：1 passed，断言 106 迁移唯一归属 business。
- `git diff --check`：通过；仅有 Git 的 CRLF/LF 提示。
- 首次 rebuild 暴露并定位到迁移映射错误；修复后第二次 `devctl.bat rebuild /y` 成功，`db-migrate` 退出码 0。`app /health`、`business-mock /health`、`mcp-service /health`、`rag-service /healthz` 均为 200；应用、RAG、MCP、PostgreSQL、Redis 容器为 healthy，其他长期后端服务处于 running。容器内 Parser 注册表包含 12 种扩展名。
- 前端 lint 未执行：任务 Worktree 与主工作区都没有 `frontend/node_modules`，全局 TypeScript/Next CLI 也不可用；未安装依赖。上传弹窗这一行扩展名配置尚未经过前端构建/浏览器交互验证。
- 第二轮定向：`test_enterprise_document_structure.py` 16 passed；结构/路由/切分/法律/表格/质量/上传/MIME 等 131 passed；索引管线/registry/lineage 90 passed；e2e 文档矩阵 30 passed、1 skipped、1 deselected（deselect 为扫描 PDF OCR 用例，在干净 main 与本机同样因 OCR 未配置失败，属既有环境基线）。
- 第二轮基线清理：`test_qa_doc_high_completeness` 改为匹配纯 FAQ 结构分 0.9 上限；`test_repaired_pdf_with_complete_pages_passes_page_coverage` 补 500 个有页码节点与第 501 页空白页，保持原“完整页通过”的验收语义。
- 第二轮 rebuild：`devctl.bat rebuild /y` 退出码 0；`db-migrate` 容器退出码 0；`agent-app`、`agent-rag-service`、`agent-rag-index-worker`、PostgreSQL、Redis 均 healthy；`app /health` 200、`rag-service /healthz` 200。
- 未做前端构建；未跑全量 pytest（全 RAG 目录超出单次执行时限）；另有 6 个与本 Diff 无关的既有红（扫描 PDF OCR 1 个、PR55 CSV 覆盖 2 个、Excel/OCR 明细 3 个），在干净 main 上同样失败。

## 下一步

向用户交付当前 Diff、验证结果和前端未验证项；保持 Worktree 修改未提交，等待用户明确后续 Git 操作。
