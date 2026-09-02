# 上传策略问题修复方案

> 生成时间：2026-08-27
> 关联分析：上传策略四问分析（目录组织 / 文档类型 / 业务库划分 / 前端两级展示）
> 关联文档：`docs/upload-test-audit-report.md`
> 涉及模块：`backend/rag/indexing/indexer.py`、`backend/app/api/routes/rag_documents.py`、`frontend/src/app/knowledge/documents/page.tsx`、`frontend/src/components/knowledge/UploadDialog.tsx`

---

## 1. 问题清单

| 编号 | 等级 | 问题 | 位置 | 一句话描述 |
|------|------|------|------|-----------|
| F1 | 🔴 严重 | 元数据管线隐性失败 | `indexer.py:1113/1094/1145` | `asyncio` 未导入、`build_llm_summary` 未导入、`enriched` 未定义，三处 NameError 被静默吞掉，所有 ≥1000 字符（或含 LLM 关键词）的文档 doc_type 落到 general、无摘要/关键词/MinHash 签名 |
| F2 | 🟠 一般 | 重索引丢失归属信息 | `rag_documents.py:302` | `POST /documents/{doc_id}/reindex` 构造 `IncrementalIndexer` 不传 kb_id/department，chunk metadata、chunk_store、registry 行被覆盖为默认值 `policy_general/general` |
| F3 | 🟡 轻微 | 文档列表页与后端不同步 | `documents/page.tsx:12-15, 232-253` | `KB_LABELS`/筛选下拉硬编码且缺 `rag_test_kb`；部门筛选不随 KB 级联；后端已支持 `doc_type` 过滤参数前端未接入（"类型"筛选走扩展名） |
| F4 | 🟡 轻微 | 扩展名白名单前后端不对齐 | `UploadDialog.tsx:251` | 前端 `accept=".pdf,.md,.txt,.docx"`，后端 `SUPPORTED_EXTS` 还含 `.xlsx/.markdown`，前端"单一来源"承诺未兑现 |
| F5 | 🟡 轻微 | 概念命名混淆（设计债） | 全局 | `type`（扩展名）与 `doc_type`（内容类型）在 UI/接口上都叫"类型"；KB 的 `domain` 与文档 `business_domain` 是两套独立体系，边界易踩混 |
| F6 | 🟠 一般（衍生） | 存量文档元数据降级未修复 | 数据层 | F1 存续期间索引的文档已落库为 general/无摘要，修复 F1 后需批量重索引回填，否则新旧文档元数据口径不一致 |

---

## 2. 修复优先级与排序理由

**修复顺序：F1 → F2 → F4 → F3 → F6（数据修复）→ F5（设计收尾）**

排序理由：

1. **F1 最优先**：影响面最大（当前几乎所有实质文档的元数据管线失效），且是纯 bug——上游不修，任何验证 F2/F6 的测试都无法得到有效元数据，等于后面白做。它直接破坏功能正确性（分类、版本快照、去重、doc_type 筛选全部失效）。
2. **F2 第二**：属于数据正确性问题——每次重索引都会**破坏**两级归属数据，属于"越操作越坏"的写入型缺陷，风险随使用累积，必须先于任何批量操作修复。
3. **F4 第三**：半小时级改动，收益立现（前端可上传 xlsx/markdown），且与 F1/F2 无耦合，可穿插进行。
4. **F3 第四**：纯前端一致性收尾，不影响数据正确性，只影响展示与筛选体验。
5. **F6 必须在 F1+F2 之后**：批量重索引依赖 F1（元数据能正确生成）和 F2（重索引不再破坏归属），顺序颠倒会造成二次数据污染。
6. **F5 最后**：设计债，涉及命名/文档/交互文案，不影响运行，放在功能修复稳定后处理。

> 安全性说明：本次未发现路径穿越、原子写、锁机制等安全问题（此前上传链路加固已覆盖），因此排序以稳定性 > 功能正确性 > 一致性 > 体验为主。

---

## 3. 逐项修复计划

### F1 — indexer.py 元数据管线三处未定义引用（🔴 严重）

**根因（已逐一核实）**

| 位置 | 缺陷 | 核实结论 |
|------|------|---------|
| `indexer.py` 模块顶部（L20-35） | 无 `import asyncio` | 已确认模块级导入只有 hashlib/json/os/pathlib/typing 等 |
| `indexer.py:1088` | `from ...metadata import enrich_metadata_llm, _extract_first_sentences` 缺 `build_llm_summary` | `build_llm_summary` 定义于 `metadata.py:627`（async、带缓存），`enrich_metadata_llm`/`_extract_first_sentences` 经 `metadata.py:15` 从 `llm_enrichment.py` 转发导入，本身可用 |
| `indexer.py:1145` | `if enriched:` 引用从未赋值的 `enriched` | 全函数无 `enriched =` 赋值语句；该块是旧"合并调用"路径残留 |

**失败路径**：`_build_doc_metadata`（L922）→ L1086 `if need_llm_keywords or need_llm_summary:` 分支 → L1113 `await asyncio.gather(...)` 抛 NameError → 被 `_index_file_inner` 的 `except Exception` 捕获 → `warning("元数据构建失败（使用默认值）")` → 上传仍报"成功"。触发条件：`need_llm_summary`（全文 ≥1000 字符）或 `need_llm_keywords`（规则关键词阶段产出 LLM 关键词对象）。

**修复思路**：最小侵入恢复并行管线可用性，不改变任务编排结构。

**改动明细**：

1. `indexer.py` 模块顶部（L20 附近）：
   ```python
   import asyncio
   import hashlib
   import json
   import os
   ```
2. `indexer.py:1088`：
   ```python
   from backend.rag.preprocessing.metadata import (
       enrich_metadata_llm, _extract_first_sentences, build_llm_summary,
   )
   ```
3. `indexer.py:1086` 前初始化（L1085 附近）：
   ```python
   enriched: dict | None = None  # 旧合并调用路径已由并行任务取代，保留分支待后续清理
   ```
   `if enriched:` 块随之成为死分支（不执行），`questions_by_chunk` 维持空列表现状（chunk 级 LLM 关键词当前已关闭，无消费方）。

**可选增强（非本次必做）**：恢复合并调用以重新产出 `questions_by_chunk`——在并行任务结果解析后，按需调用 `enrich_metadata_llm(full_text, doc_type, chunks_text)` 并赋值 `enriched`。建议作为独立后续任务，本次不做，避免引入新的 LLM 调用面。

**预期效果**：≥1000 字符文档的 `doc_type/confidence/summary/keywords_llm/entities/minhash_sig/quality_score` 全部恢复产出；连带恢复 financial 版本快照（`is_latest=False` 标记）、MinHash 同类型分桶去重、doc_type 筛选、词库页动态词库联动。

**改动范围**：1 个文件，约 4 行。

---

### F2 — reindex 端点丢失 kb_id/department（🟠 一般）

**根因（已核实）**：`rag_documents.py:302-305` 构造 `IncrementalIndexer` 时只传 5 个位置参数 + `bm25_store`，`kb_id`/`department` 落入默认值 `policy_general`/`general`（`indexer.py:94-95`）。且 `indexer.py:257` 的逻辑是 `kb_id = self.kb_id if self.kb_id != "default" else self._derive_kb_id(file_path)`——默认值是 `policy_general` 而非 `default`，**连路径反推兜底都被短路**。`self.department` 则被直接写入 chunk metadata（L511/570/618）与 registry（L1224），以及 doc_id 派生（L1376）。

**修复思路**：从 registry 现存记录回读归属，显式传入 indexer，与上传路径行为对齐。

**改动明细**（`rag_documents.py`，reindex 函数内，L296 之后）：

```python
# P1: 从 registry 回读归属，避免重索引覆盖为默认值（与 upload 路径对齐）
reg_kb = doc.get("kb_id") or ""
reg_dept = doc.get("department") or ""
indexer = IncrementalIndexer(
    DOCS_DIRECTORY, pipeline.vectordb, pipeline.doc_db, pipeline.embedding, reg,
    kb_id=reg_kb or "default",          # "default" 触发 _derive_kb_id 路径反推兜底
    department=reg_dept or "general",
    bm25_store=pipeline.bm25_store,
)
```

注意 `kb_id` 传 `"default"`（而非 `"policy_general"`）才能命中 L257 的路径反推兜底，与磁盘扫描语义一致。

**预期效果**：重索引后 chunk metadata、chunk_store、registry 行的 kb_id/department 与原值一致；检索层 metadata_filter（部门隔离）不再因重索引而失效。

**改动范围**：1 个文件，约 6 行。

**附加建议（可选）**：前端批量重建（`documents/page.tsx` 的 batchReindexing 循环调单文档端点）无需改动，自动受益。

---

### F4 — 前端上传 accept 白名单缺 .xlsx/.markdown（🟡 轻微）

**修复思路**：补齐 accept；若追求真正的"单一来源"，后端新增一个只读接口（或复用 `/stats` 返回 `supported_exts`），前端动态渲染。分两步：

**改动明细**：

1. **立即（硬编码补齐）** — `UploadDialog.tsx:251`：
   ```tsx
   accept=".pdf,.md,.txt,.docx,.xlsx,.markdown"
   ```
   同时检查同文件内是否有前端扩展名校验逻辑（如有同步补齐）。
2. **后续（单一来源，可选）** — 后端在 `/api/rag/stats` 响应中增加 `supported_exts: [...PARSABLE_EXTS]`；前端上传对话框拉取后动态拼接 accept 字符串。删除前端硬编码。

**预期效果**：用户可在前端直接选择 xlsx/markdown 文件；后端不再出现"前端拦得住的合法类型被拒"或"前端放行后端拒"的错位。

**改动范围**：1 个文件 1 行（立即）；后端 1 处 + 前端 1 处（可选增强）。

---

### F3 — 文档列表页两级展示与筛选不同步（🟡 轻微）

**三个子问题**：

| 子项 | 现状 | 后端能力 |
|------|------|---------|
| KB 标签与选项 | `KB_LABELS` + 下拉选项硬编码，缺 `rag_test_kb`（裸显 id） | `/api/rag/knowledge-bases` 已返回 id/name/depts |
| 部门筛选 | 列出全部 9 部门，不随 KB 级联，可选到后端必拒的组合 | kb 的 `depts` 字段即该库合法部门 |
| 类型筛选 | "类型"筛选按扩展名（pdf/md/txt/docx） | `/documents` 已支持 `doc_type` 参数（`rag_documents.py:51`），14 类 |

**修复思路**：列表页与上传对话框统一数据源——都从 `GET /api/rag/knowledge-bases` 动态取 KB 列表；部门选项按所选 KB 级联；"类型"筛选改接 `doc_type`，扩展名筛选改名"格式"保留。

**改动明细**（`documents/page.tsx` + `hooks/useKnowledge.ts`）：

1. 删除 `KB_LABELS` 硬编码（L12-15），改用 `useKnowledgeBases()`（若 hook 不存在则在 useKnowledge.ts 中新增，内部调 `knowledgeService.getKnowledgeBases()`）：
   - 表格 KB 列：`kbList.find(k => k.id === d.kb_id)?.name ?? d.kb_id`；
   - KB 筛选下拉：`kbList.map(k => <option value={k.id}>{k.name}</option>)`。
2. 部门筛选级联：KB 选中时，部门选项 = 该 KB 的 `depts`（映射中文标签）；切换 KB 时若当前 dept 不在新列表内则重置为 `""`（全部）。KB 为空时部门选项保留全量（与现状一致）。
3. 类型筛选拆分：
   - 新增"类型"下拉（doc_type）：14 类 + 全部，选项文案沿用 `DOC_TYPE_CN`（已存在 L20）；选中时向 API 传 `doc_type=xxx`；
   - 原扩展名下拉改标签为"格式"，传参仍为 `type`。
   - `useDocuments` 增加 `docType/setDocType` 状态与请求参数（服务层 `knowledge.ts` 的 `getDocuments` 增加 `doc_type` query 参数）。

**预期效果**：KB 标签零遗漏（新增库无需改前端）；部门筛选不可再选出无效组合；内容类型筛选真正生效（此前形同虚设）。与上传对话框的两级级联交互对齐，前后端一份事实来源。

**改动范围**：2-3 个前端文件，约 80-120 行。

---

### F6 — 存量文档元数据回填（🟠 一般，衍生）

**问题**：F1 存续期间入库的文档 registry 中 doc_type=general、summary 为空、minhash_sig 为空/旧值。修复 F1 后**新上传正常，但存量数据不会自愈**，导致 doc_type 筛选、MinHash 去重、financial 版本快照对存量文档持续失效。

**修复思路**：写一次性脚本批量重索引（不是简单 sync——sync 按文件 hash 判断 UNCHANGED 会跳过）。

**改动明细**（新增 `scripts/backfill_metadata.py`）：

1. 通过 registry 列出全部 active 文档（按 `doc_type == 'general'` 或 `summary` 为空筛选受影响集合，也支持 `--all`）；
2. 逐个调用与 reindex 端点相同的核心逻辑（构造 `IncrementalIndexer` 时**按 F2 修复后方式传入回读的 kb_id/department**），或直接循环调用 `POST /api/rag/documents/{doc_id}/reindex`（后端服务运行中时更简单，且自动获得 operation_log 留痕）；
3. 串行 + 间隔（如 0.5s）执行，避免 LLM/embedding 并发雪崩；输出回填前后 doc_type/summary 对比报告。

**预期效果**：存量文档元数据与新口径一致；MinHash 签名补齐后近似去重恢复生效。

**改动范围**：新增 1 个脚本（约 60-80 行），无存量代码改动。

**前置依赖：F1、F2 必须已修复并验证**——否则回填要么继续产出 general，要么把 kb/department 覆盖成默认值（二次污染，比不修更糟）。

---

### F5 — 概念命名与文档收口（🟡 轻微，设计债）

**问题**：三套"类型"体系（扩展名 / doc_type / business_domain）+ 两套"域"（KB.domain / doc.business_domain）在 UI 文案与接口参数命名上重叠。

**修复思路**：不改代码行为，只做命名与文档澄清（避免大规模重构风险）：

1. UI 文案层面（配合 F3 一起做）：
   - 扩展名筛选/列头改叫"**格式**"；
   - doc_type 列与筛选改叫"**内容类型**"；
   - `business_domain` 列改叫"**业务域**"（内容推导），与知识库的"业务方向"（库属性）在页面分组标题上区分。
2. 文档层面：在 `docs/RAG_DESIGN.md` 增加一节"三套类型体系与两套域的边界"，写明各自来源（扩展名=入口校验/解析器选择；doc_type=内容分类+差异化处理开关；business_domain=内容标签；KB.domain=库级静态标签）。
3. 接口层面（可选，需向后兼容）：`/documents` 的 `type` 参数保留别名、文档标注推荐用 `ext`；此条涉及 API 兼容性，单独立项评审，不并入本次。

**预期效果**：消除此前词库页"业务场景二级分组"踩到的概念重叠隐患；后续开发者与用户不再混淆。

**改动范围**：前端文案若干处 + 文档 1 节；接口改名单独立项。

---

## 4. 依赖关系与实施顺序

```
F1 (indexer 元数据) ──┬──> F6 (存量回填) ──> F5 (设计收口)
F2 (reindex 归属)  ───┘        ↑
F4 (accept 白名单) ──(无依赖，可并行)──┘
F3 (前端列表页)   ──(无依赖，可与 F1/F2 并行)──┘
```

| 修复 | 依赖 | 被依赖 | 可并行性 |
|------|------|--------|---------|
| F1 | 无 | F6 | 可与 F2/F3/F4 并行开发 |
| F2 | 无 | F6 | 可与 F1/F3/F4 并行开发 |
| F4 | 无 | 无 | 独立，随时可做 |
| F3 | 无（仅消费现有接口） | F5（文案部分共用改动） | 独立 |
| F6 | **F1 + F2（均需验证通过）** | 无 | 必须最后执行 |
| F5 | F3（文案改动合并做） | 无 | 收尾 |

**推荐批次**：
- **批次一（同一 PR/同一提交窗口）**：F1 + F2 + F4 —— 后端正确性三连修，改动小、可统一回归。
- **批次二**：F3 —— 纯前端。
- **批次三**：F6 —— 数据修复（运行脚本 + 抽查）。
- **批次四**：F5 —— 文档与文案收口。

**关键红线：F6 执行前必须确认 F1、F2 均已合入并通过验收**。违反顺序的后果：批量重索引把全库 kb/department 覆盖为 `policy_general/general`（数据污染），或继续产出 general 元数据（无效回填）。

---

## 5. 风险评估与回滚方案

| 修复 | 主要风险 | 等级 | 缓解措施 | 回滚方案 |
|------|---------|------|---------|---------|
| F1 | ① 修复后 LLM 调用恢复（摘要/关键词），索引延迟上升、Ollama/LLM 服务压力增大；② 存量与新文档 doc_type 口径不一致（修复前 general、修复后真实类型），MinHash 可能突然检出大量"近似文档"告警；③ `enriched` 死分支保留，存在再次误用隐患 | 中 | `build_llm_summary` 自带缓存（`build_llm_summary_cached`）；gather 已用 `return_exceptions=True` 单任务失败不中断；F6 回填统一口径；死分支加注释说明 | 单文件 ~4 行改动，`git revert` 即回滚；回滚后系统回到"静默降级但可上传"的已知状态 |
| F2 | ① 回读的 registry 记录若本身 kb/department 为空（历史脏数据），仍落入兜底值；② doc_id 派生协议（md5(kb\|dept\|basename)）与旧协议并存，重索引后 doc_id 变化的文档需确认复用 active 记录逻辑正常 | 中低 | 传 `"default"` 走路径反推兜底；执行前先 SQL 抽查 `SELECT kb_id, department, COUNT(*) FROM documents GROUP BY 1,2` 确认脏数据量；F6 回填前后做 doc_id 稳定性抽查 | 单文件 ~6 行，`git revert`；已错误覆盖的归属可由 F6 脚本从目录路径反推修正（目录即两级归属的事实来源） |
| F4 | accept 补齐后用户上传 xlsx 增多，解析/索引耗时上升；xlsx 解析器若有未暴露的 bug 会浮出水面 | 低 | 上传链路已有质量门禁与失败留痕；灰度观察即可 | 删除 accept 中的新扩展名即回滚 |
| F3 | 前端改动态拉取后，`/knowledge-bases` 接口异常会导致列表页标签/筛选整体退化（原硬编码不受影响） | 低 | hook 内做失败兜底（回退到本地最小映射或显示 id）；接口本身轻量只读 | 前端独立部署，revert 前端 commit 即回滚，不触后端 |
| F6 | 批量重索引期间 LLM/embedding 高负载；中途失败造成部分回填部分未回填 | 中 | 串行 + 间隔 + 幂等（脚本按 registry 状态筛选，可重跑续传）；先拿 5-10 篇试运行验收再全量 | 脚本不改存量代码、registry 有状态留痕；重索引本身不删除向量以外的数据，`.bak` 备份与软删机制仍在；极端情况可用 `database.db` 备份整体还原（执行前先备份 db 与 DOCS 目录） |
| F5 | 文案改动可能影响既有 UI 测试快照 | 低 | 同步更新测试断言 | revert 即回滚 |

**通用回滚保障**：批次一合入前打 git tag（如 `pre-upload-fix`）；F6 执行前备份 `database.db`（含 -shm/-wal 先停写）与 `data` 文档目录。

---

## 6. 验证方式（验收标准）

### F1 验收
1. **静态检查**：`python -c "import backend.rag.indexing.indexer"` 无警告；`ast` 扫描 `_build_doc_metadata` 内不再有未定义名称（可用 `pyflakes indexer.py`）。
2. **单元测试**（建议新增 `tests/test_metadata_pipeline.py`）：
   - 构造 ≥1000 字符样例文本，调用 `_build_doc_metadata`（mock embedding/LLM），断言返回 dict 含非空 `summary`、`doc_type != None`、`minhash_sig` 非空；
   - 构造 <1000 字符文本，断言 summary == 全文；
   - 断言全程无 "元数据构建失败（使用默认值）" 日志。
3. **端到端**：上传一篇 ≥1000 字符的制度类文档 → `/documents` 列表查看 `doc_type` 应为 policy（而非 general）、summary 非空、`llm_used` 有值。

### F2 验收
1. 上传文档到 `biz_inventory/warehouse` → 记录 kb_id/department；
2. 调 `POST /documents/{doc_id}/reindex` → 重新查 `/documents`：kb_id/department 不变；
3. 检索验证：带 `metadata_filter={"department": "warehouse"}` 的查询仍能命中该文档 chunk；
4. `SELECT kb_id, department FROM ... WHERE doc_id=...` 确认 registry 行未被覆盖。

### F4 验收
前端上传对话框文件选择器可选 `.xlsx` / `.markdown` 文件且上传成功入库。

### F3 验收
1. 列表页 KB 列/筛选出现 `rag_test_kb`（显示中文名而非裸 id）；
2. 选中 `biz_inventory` 后部门下拉只剩该库 `depts`；切换 KB 时无效部门选中态被重置；
3. 选 doc_type=policy 筛选，请求参数含 `doc_type=policy`，返回列表全部 doc_type=policy；
4. `getKnowledgeBases` 接口断开（mock 500）时页面不崩，回退兜底展示。

### F6 验收
1. 脚本 dry-run 报告：受影响文档清单、预计耗时；
2. 全量执行后：`SELECT doc_type, COUNT(*) GROUP BY 1` 中 general 占比显著下降（抽样人工核对分类是否合理）；
3. 抽 10 篇确认 kb_id/department 与目录结构一致、summary 非空；
4. MinHash：上传一篇与存量近似的文档，日志出现 `[MinHash] 检测到近似文档`。

### 回归
- 跑既有 `pytest tests/`（上传/索引相关用例）全绿；
- 前端 `npm run build`（或项目等价命令）无类型错误。

---

## 7. 工作量预估与进度安排

| 修复 | 开发 | 测试/验证 | 合计 |
|------|------|----------|------|
| F1 | 0.5h（3 处改动 + 注释） | 1.5h（单测 + 端到端） | **2h** |
| F2 | 0.5h | 1h（含 SQL 抽查与检索验证） | **1.5h** |
| F4 | 0.25h | 0.25h | **0.5h** |
| F3 | 2h（hook + 页面 + 服务层） | 1h | **3h** |
| F6 | 1h（脚本） | 1-2h（试运行 + 全量 + 抽查，视文档量） | **2-3h** |
| F5 | 1h（文案 + 文档） | 0.5h | **1.5h** |
| **合计** | | | **约 10.5-11.5h ≈ 1.5 人天** |

**建议排期（按天）**：

| 时间 | 内容 | 里程碑 |
|------|------|--------|
| D1 上午 | F1 + F2 + F4 编码，`pre-upload-fix` tag | 后端三修合入 |
| D1 下午 | F1/F2 验收测试（新增单测 + 端到端） | ✅ 后端正确性恢复 |
| D2 上午 | F3 编码 + 自测 | 前端一致性合入 |
| D2 下午 | F6：备份 db/DOCS → 试运行 5-10 篇 → 全量回填 → 抽查 | ✅ 存量数据口径统一 |
| D3（半天） | F5 文案/文档收口 + 整体回归 | ✅ 全部完成 |

> 若时间紧张，最小可交付集为 F1 + F2 + F6（约 1 人天），F3/F4/F5 可延后——它们不影响数据正确性，只影响体验与一致性。
