# Batch A 修复报告

日期：2026-10-14（Asia/Shanghai）

基线：`cfa482e0bce5b1979f82304cde43bdf7d20dac5e`

范围：仅 Batch A 内容完整性与发布安全。Batch B/C 未开始。
`RAG_UPLOAD_E2E_VERIFIED`：**false**。

## 根因与最小修复

| 问题 | 根因 | 修复 |
| --- | --- | --- |
| FAQ 问题文本丢失 | `DocumentCleaner._remove_isolated_caption_lines` 只把中文标点当句读；以 ASCII `?` 结尾的独立问句命中图注启发式，Cleaner 将 40 个问题节点清空。 | [cleaner.py](../../../backend/rag/preprocessing/cleaner.py) 将常见 ASCII 句读纳入保护条件；不改 QA 切分策略。 |
| 40 个 FAQ Leaf 的 Parent 引用悬空 | 40 个空问句使虚拟 Parent 仅剩 `Q：` 行并被 `ChunkFilter` 过滤。索引 ID 映射只处理最终仍存在的 Chunk，未发现 Leaf 保留了旧 Parent ID。 | [indexer.py](../../../backend/rag/indexing/indexer.py) 在过滤后、质量门禁前统一索引 ID；[quality_gate.py](../../../backend/rag/preprocessing/quality_gate.py) 检查非空 Leaf、Chunk ID 唯一性和每条显式 Parent 引用是否指向唯一的 Parent Chunk。无效关系硬失败，不补造或静默清空 Parent。 |
| CSV 零 Chunk 仍记录完整覆盖 | Indexer 把 Parser/Chunking 阶段、过滤前的 Coverage 用于最终质量记录；CSV 候选全部被 Filter 拒绝，旧 Coverage 仍报告全覆盖。 | [indexer.py](../../../backend/rag/indexing/indexer.py) 在 Filter 后用最终 Chunk 重算 Coverage；零 Leaf 和缺口由现有 QualityGate 硬失败。 |
| 完整性门禁与 Trace/发布状态不一致 | 质量记录只保留 Coverage，不对最终 Chunk/Parent 关系对账；记录构建异常时只对 Coverage 做 fail-closed。 | 复用既有 QualityGate，在 Quality Record、`quality_issues`、`index_quality` Trace 中写入 Chunk 完整性摘要；Coverage 或 Chunk 完整性异常时阻断写向量/发布。candidate mode 不更新上一代 Active registry。 |
| 自动 Review：PII 脱敏导致 Coverage 误报 | 索引输出 Leaf 经 PII 掩码后与未掩码 AST 逐字对账，真实覆盖会被判为缺口。 | `coverage_audit` 仅在最终 Leaf 标记 `pii_masked` 时额外建立掩码态对照；未脱敏 Leaf 始终按原文核验，避免全局归一化把未脱敏内容缺失掩盖。Index Quality Record 保留 `pii_mask_normalized` 审计标记。 |
| 自动 Review：短 FAQ Parent 被过滤 | 非空问题 Parent 短于通用最小长度阈值，虽然仍被有效 Leaf 引用，却会被过滤，造成悬空 Parent 引用和候选失败。 | Indexer 在既有过滤点保留非空且仍被 Leaf 引用的 Parent，记录 `filter_status=kept_referenced_parent` 与原过滤原因；启用 PII 时仍执行掩码，不另建 FAQ 切分机制。 |

`coverage_audit` 本身已有“只检查 Leaf、表格逐行及逐单元格核对”的实现，本轮没有重写它。修复将其调用时点改为最终 Filter 输出；抽样审计仍遵循既有 `sampled` 留痕口径。

## 失败前复现

原始真实测试报告及数据库证据位于 [合并后 E2E 报告](../rag-upload-postmerge-e2e-20261014/REPORT.md) 与 [基线证据](evidence/batch-a-baseline.md)。原报告副本 SHA256 与源文件一致：`4CC0324E099F1110A2D5DE93FBDA114D62E03612A868FD08145D7D01FF63CE97`。

修复前在 `cfa482e0` 上新增回归用例，实际结果为 **5 failed、2 passed**：

- FAQ DOCX 测试：3 个真实 `qa_question` 均被清洗为空。
- 零 Leaf 与悬空 Parent 完整性测试：Quality Record 无 Chunk 完整性审计。
- CSV 候选被全部 Filter 后，质量记录仍使用预过滤 Coverage，测试随后观察到 Indexer 继续写入路径；本机 PostgreSQL 因无密码连接失败。
- 既有 Coverage 缺口测试的 2 个场景通过，证明原门禁只覆盖已经识别出的缺口。

## 修复后验证

- 最终定向命令覆盖 FAQ 路由、QualityGate、索引失败收口、Parent/Child 检索、Cleaner、Chunk Governance、16 类本地文档矩阵及长期文档治理模块：**106 passed，1 skipped，1 deselected**。
- FAQ 回归验证 3 个问题保留；索引 canonical ID 为 `faq-doc_0` Parent 与 `faq-doc_1...` Leaf，所有 Leaf 的 `parent_chunk_id=faq-doc_0`。通过现有 `attach_parent_context` 命中一个 Leaf 并实际取回含问题文本的 Parent。此项是本地流水线/检索函数验证，不是 API 或数据库 E2E。
- CSV 回归先确认过滤前完整覆盖，再拒绝全部 Chunk；最终 Quality Record 记录 0 Chunk、2/2 行及 6/6 单元格缺失，`chunks_present` 与 `coverage_gap` 为硬异常，Trace 有相同 Coverage 摘要；向量写入调用为 0。
- 悬空 Parent 回归在 Indexer 级进入 Quality Record、`quality_issues` 与 Trace，导致候选失败；candidate mode 的旧 Active registry 未被更新，且向量写入为 0。
- `py_compile` 覆盖全部本轮修改的 Python 源码与测试文件：通过。`git diff --check`：通过。
- Ruff 对 6 个修改 Python 文件报告 17 条 lint 项；把相同文件从基线 HEAD 逐个送入 Ruff 后也是相同的 17 条，修复未新增 lint 项。它们是基线既有 import/unused/重复键/变量名问题，本轮不顺手扩大清理范围。
- 本机 PostgreSQL registry 专用用例**BLOCKED**：单独实际运行失败，错误为 `fe_sendauth: no password supplied`。总数中的 1 deselected 是为避免重跑该环境依赖测试；没有标记为 PASS。
- 自动 Review 两条边界回归在修复前为 **2 failed**；修复后专项回归 **3 passed**，包括“脱敏 Leaf 可覆盖”和“未脱敏 Leaf 缺失仍被发现”。
- 修复后的最终定向测试命令覆盖相同 9 个 RAG 测试模块，结果 **109 passed、1 skipped、1 deselected**。其中 16 类本地文档矩阵通过；1 skipped 为既有条件跳过，1 deselected 为 PostgreSQL 凭据阻塞用例。
- `py_compile`（本轮修改的 indexer、coverage 审计与两组测试文件）及 `git diff --check` 均通过。PR #58 代码修复 HEAD `b0a3771a431fb87e6387795a6956742b045c3c20` 的 Secret Scan、RAG Smoke preflight、8-case RAG Smoke 均通过；真实 API 评测 job 按边界为 skipped。两条自动 Review 线程已 resolved。

## 需要重建/继续调查的隔离测试索引

本轮未写入索引；以下动作待代码合并/部署后由 Batch C 在原隔离 KB 重新上传验证：

| 文档 | 旧 doc_id / upload_id | 处置 |
| --- | --- | --- |
| `faq_only.docx` | `29555be9a7` / `549c7be26ff8` | Active 内容丢失且 40/40 Parent 引用无效；必须重建并核验 Leaf、canonical Parent ID 与检索扩展。 |
| `help_center_mixed.md` | `52a4bacfad` / `725108738d3c` | Active，但报告记录 Q2/A2、Q10/A10 跨 Chunk；本轮只修复独立 FAQ 清洗/完整性，不修改混合文档切分策略。先在 Batch C 复核；只有对应切分缺陷修复后再重建。 |
| `sku.csv`、`sku_bom.csv`、`sku_gbk.csv` | `2349c6976cf6`、`9cd01a6dfc76`、`305ce3bd5688` | 三份均失败且没有 Active 输出；修复后在 Batch C 重传验证，不存在需要清理的已发布 Chunk。 |

## 阻断与遗留风险

- **真实 E2E 未复验**：修复后尚未启动真实 API/Worker/数据库，不曾写入 `rag_eval_kb` 或调用真实 Search API；不满足改 `RAG_UPLOAD_E2E_VERIFIED=true` 的条件。
- **Batch B 未处理**：PDF `.strip()` 解析异常、Excel 遗漏、长 TXT Worker OOM/`RUNNING` 恢复、OCR 依赖、migration 092/093 漂移都保持原报告中的 FAIL/BLOCKED 状态。
- **PostgreSQL 环境阻塞**：本机测试连接没有密码；未伪造 PASS，也未修改数据库认证配置。
- **Clash/模型连接**：未调用管理端模型连接测试，未修改代理配置，未运行付费 Provider 评测；该猜测尚未验证，与本轮本地 FAQ/Chunk 门禁无关。
- **真实服务链路**：当前证据限于源码、本地 Parser/Indexer 与检索函数单测；未连接隔离 KB 的真实 Upload API、Worker、向量库或 Search API。PR #58 当前 Open / Mergeable；Review 线程已解决，CI 通过不构成真实 E2E 验收。
- 需要在 Batch C 观察真实索引物理 ID 与 metadata canonical ID 的一致性、真实检索 Parent 扩展、旧 Active 保护及权限对照。当前本地测试不能替代这些证据。

## 结论

Batch A 的源码级复现与定向回归通过，候选内容完整性失败会在向量写入前阻断，candidate mode 保留旧 Active。当前仅建议审阅 Batch A 修复 PR；**不建议宣称 RAG 上传 E2E 验收通过**，`RAG_UPLOAD_E2E_VERIFIED` 继续为 `false`。本轮在此停止，不进入 Batch B 或 Batch C。
