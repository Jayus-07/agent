# Batch B1：多格式内容完整性修复

## 目标

基于 PR #58 合并提交 `1acf6a4b34fdae4ba257a2e9bfcb1a24ee621d01`，并在最新 `origin/main`（`bf3cddf7ac7329f576f036cb1a9717a8f96dfd27`）上修复 CSV 表格过滤、PDF 空值解析和 Excel 多级表头覆盖缺口；确认相关长文档 Coverage 缺口是否属于同一切块边界根因。复用现有路由、Chunk、Coverage、QualityGate 和发布流程。

## 范围

- CSV：数字型/短表格行按表格类型保留；SKU、订单号、数字和编码内容精度不丢。
- PDF：修复 PyMuPDF 表格空单元格触发的解析异常；复核页码、顺序、普通 PDF 与长英文 PDF 的过滤行为。
- Excel：保留多级表头源内容，修复 `multi_sheet.xlsx` 源行/单元格未覆盖问题。
- 如证据证明为同一审计根因，修复长文档切块边界导致的 Coverage 误报。
- 新增针对性失败复现与回归；复用原隔离 Compose、隔离 KB 验证真实 API→Worker→Index→Search。

## 停止条件

B1 代码、定向测试、真实上传与检索证据和报告完成后停止。不得启动 B2/B3/C，不跑付费 Provider，不修改正式数据库，不合并 main，不将 `RAG_UPLOAD_E2E_VERIFIED` 改为 true。
