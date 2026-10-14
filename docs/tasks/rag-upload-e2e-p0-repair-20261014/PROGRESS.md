# Progress

## 基线

- 分支：`codex/rag-upload-e2e-p0-20261014`
- Worktree：`C:\Users\wh\.codex\worktrees\rag-upload-p0-batch-a\agent`
- 起始 HEAD：`cfa482e0bce5b1979f82304cde43bdf7d20dac5e`（与 origin/main 核对一致）
- 原 E2E Worktree 未修改；原始报告已按原文复制到 `docs/tasks/rag-upload-postmerge-e2e-20261014/REPORT.md`。
- Batch A 原始数据库与本地流水线复现见 `evidence/batch-a-baseline.md`。

## 已确认根因

- Cleaner 的孤立图注规则把末尾为 ASCII `?` 的独立中文问题删除；40 个问题节点因此为空。
- FAQ 的虚拟 Parent 因只含空 `Q：` 行被 `ChunkFilter` 移除；40 个 Leaf 仍保留指向该 Parent 的旧 ID。索引 ID 映射只改写存在于最终 Chunk 集合中的 ID，无法发现悬空引用。
- Indexer 使用 parse 阶段、过滤前计算的 Coverage 构建质量记录；CSV 候选全部被过滤时，记录仍可能保留预过滤的完整 Coverage。
- PR #58 自动 Codex Review 又发现两个边界：脱敏后的最终 Leaf 与未脱敏 AST 直接比较会误报覆盖缺口；FAQ 短 Parent 被长度过滤后会使仍有效的 Leaf Parent 引用悬空。

## 执行记录

- 2026-10-14：只读查询隔离数据库副本，FAQ `29555be9a7` 为 Active / Quality pass，40 Leaf、0 Parent、40 个无效 Parent 引用；三份 CSV 的 IndexRun 都以 0 Chunk failed。没有更改原 E2E 数据卷。
- 2026-10-14：直接在基线源码调用真实 FAQ DOCX Parser→Cleaner→QA 切分→ChunkFilter→索引 ID 映射，得到 40 个问题节点、0 个非空问题；Parent 被过滤，40/40 Leaf Parent 引用悬空。
- 失败前新增回归实跑：5 failed、2 passed。FAQ 问句为空、Integrity 字段缺失、过滤后 CSV Coverage 仍完整均被复现；零 Chunk 路径随后因本机 PostgreSQL 无密码而失败，没有误记 PASS。
- 最小修复：Cleaner 保护 ASCII 句读；Indexer 过滤后生成 canonical Chunk ID 并重新审计 Coverage；既有 QualityGate 记录并硬校验 Leaf/ID/Parent 完整性；Quality Record、Trace、quality_issues 记录摘要，候选不完整时不写向量且不更新旧 Active registry。
- Review 意见修复采用最小处理：Coverage 仅对最终明确标记 `pii_masked` 的 Leaf 与源文生成同态脱敏对照，其他 Leaf 继续按原文核对；过滤器保留非空且仍被 Leaf 引用的短 Parent，并记录过滤覆盖原因及脱敏状态。
- 两个 Review 回归在修复前各自失败（短 Parent 变成 0 个；脱敏内容无法传入 Coverage 审计），修复后专项回归 **3 passed**。另增加混合脱敏/未脱敏测试，确认归一化不会掩盖未脱敏 Leaf 的内容丢失。
- 最终定向回归：**109 passed、1 skipped、1 deselected**（本机 PostgreSQL registry 环境依赖测试）。16 类本地文档矩阵、Chunk Governance、FAQ Parent 扩展、索引质量门禁均包含在实际命令中。
- Python `py_compile` 与 `git diff --check` 通过。
- Ruff 对当前 4 个受影响 Python 文件检查为 17 条；相同文件在父提交也是 17 条，本轮未新增。`py_compile` 和 `git diff --check` 通过。
- 本机 PostgreSQL registry 测试单独执行为 BLOCKED：`fe_sendauth: no password supplied`。未更改数据库配置，也未连接正式业务库。
- 原始 E2E 报告原文副本 SHA256 与来源一致；FAQ 失败索引及需后续复核的混合 FAQ 索引写入 `BATCH_A_REPAIR_REPORT.md`。没有对测试库或正式索引执行重建。
- 修复 PR：[#58](https://github.com/Jayus-07/agent/pull/58)，分支 `codex/rag-upload-e2e-p0-20261014`；首个提交 `80d9dac5426d76097ec3f426ad198b772fbee430` 上的 Secret Scan、RAG Smoke preflight 和 8-case RAG Smoke 均通过，真实 API 评测 job 按约定跳过。自动 Review 两条意见已在代码和回归中处理；最新补丁提交后的 CI/Review 线程状态待补录。

## 停止边界

Batch A 完成后停止；不进入 Batch B/C。修复后真实 API、Worker、数据库、向量索引与 Search E2E 尚未执行，`RAG_UPLOAD_E2E_VERIFIED` 保持 `false`。不合并 main；最终 HEAD、补丁提交后的 CI 和 Review 状态补入本文件。
