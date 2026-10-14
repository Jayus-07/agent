# B1 施工进度

- 状态：B1 代码与验收收口完成；等待 PR/Review/CI；不启动 B2/B3/C。
- Worktree：`C:\Users\wh\.codex\worktrees\rag-upload-b1-20261014\agent`
- 分支：`codex/rag-upload-b1-20261014`
- 基线：PR #58 merge `1acf6a4b34fdae4ba257a2e9bfcb1a24ee621d01`；施工起点为最新 main `bf3cddf7ac7329f576f036cb1a9717a8f96dfd27`。
- 当前改动尚未提交；提交 SHA 与 PR/CI 状态将在推送后补记。
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

## PR / 下一步

1. T0/T1/T2 已收口，真实 CSV/Excel 通过；PDF 端到端仍阻塞。报告已建议 Draft PR，当前不建议合并。
2. 提交并推送独立 B1 分支，创建 Draft PR，附上报告；不自动合并 main。
3. 核对必需 CI/Review；如果 PR 审查或 CI 引出 B1 范围问题，只处理对应增量。
4. 本轮到此停止，不启动 B2/B3/C，不跑付费 Provider，不修改正式数据库或全局验收状态。
