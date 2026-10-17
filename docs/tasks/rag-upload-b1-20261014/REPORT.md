# Batch B1 多格式内容完整性修复与真实验收报告

日期：2026-10-14
分支：`codex/rag-upload-b1-20261014`
施工基线：`bf3cddf7ac7329f576f036cb1a9717a8f96dfd27`（PR #58 合并后最新 main）
隔离环境：Compose `rag-pr55-e2e`，隔离 KB `rag_eval_kb`
全局验收状态：`RAG_UPLOAD_E2E_VERIFIED=false`

## 结论摘要

- CSV 三种编码/格式变体均通过真实 API、Worker、向量索引和 Search；SKU 与数字内容可检索，Parent 关系有效。
- `multi_sheet.xlsx` 经真实上传、Worker 重索引、数据库对账和 Search 验证通过；Coverage 记入 89/89 行、320 个单元格，4/4 源表节点覆盖，89 个 Leaf 的 Parent 引用全部有效。
- PDF Parser 的空值异常已在解析层修复并通过定向测试及样本解析；但真实 PDF 上传在 Worker 子进程 SIGKILL 后没有形成最终 IndexRun/终态/Search 证据。是否 OOM 尚未由本轮证据确认，PDF 全链路为 **BLOCKED**，不将解析成功等同于上传链路通过。
- 因 PDF 实际索引检索仍阻塞，不建议合并本 PR。按任务边界停止于 B1；不启动 B2/B3/C，不运行付费 Provider，不改正式数据，不合并 main。

## 根因与修复

### CSV：结构化短行被正文过滤器误删

ChunkFilter 将表格 Leaf 当作普通正文，应用低中文比例、最小长度及相似度去重。SKU、编码、数字行因此被过滤，原 E2E 中 21/22 个 Leaf 被丢弃并产出 0 个有效 Leaf。

修复在识别为表格的既有 Chunk 类型上加入类型感知过滤：表格行、表头和表格回退块不再套用正文中文比例/短文本/相似度规则；一般正文仍保留原过滤门槛。表格行保留键值及原始行信息，SKU 前导零、长数字和编码语义未转为数值。

### PDF：缺失文本和空表格单元格未做 None 安全处理

PDF span 的 `text` 字段及表格单元格可能不存在或为 `None`，旧逻辑直接调用字符串方法导致 `NoneType` 异常。Parser 与表格自然语言转换现对缺失值安全处理。另修正文清理误删引用中的年份/编号，并让过长法律正文沿既有预算切分路径连续分段，使 Coverage 可按声明的 split group 重建；有意义的英文正文按既有过滤器的保守拉丁文本阈值保留，其他过滤规则不关闭。

### Excel：多级表头未成为输出 Leaf，公式缓存缺失时内容丢失

多级表头只参与内部列名构造、没有形成实际输出 Leaf；公式缓存值为空时也会丢失公式原文。现在将源表头作为 `table_header` Leaf 输出；同时读取缓存值和公式视图，优先使用缓存结果，无缓存时保留公式表达式。Coverage 审计增加已声明 split group 的拼接、跨相邻 Leaf 的连续内容检查和表格行身份核验，并且只以实际输出 Leaf 计覆盖。新增回归覆盖重复表格行缺失、单行 Sheet 和超长表格行，防止部分行/单元格丢失仍假通过。

未新增 Chunk 策略或任务恢复系统，也未降低 QualityGate。

## 真实链路证据

所有记录来自隔离 KB `rag_eval_kb`，无正式业务数据访问。CSV 使用本地 BGE-M3；无付费 Provider 调用。

| 样本 | 真实 API / IndexRun | 入库与质量对账 | Search | 状态 |
|---|---|---|---|---|
| `sku.csv` | upload `02658209866e`；doc `f92bb0e8ee`；IndexRun `c6609797-40be-4e86-be3e-a67445b11b18` | Active；502 Chunks（501 Leaf / 1 Parent）；501/501 Parent 引用有效；Coverage 501 行、2004 单元格 | 返回 SKU `00532931` 正确行，来源 `b1-sku.csv` | PASS |
| `sku_bom.csv` | upload `7f984256610b`；doc `8c5377b828`；IndexRun `a44076bb-92b4-447b-9822-bea04bdc0af0` | Active；502 Chunks（501 Leaf / 1 Parent）；数据库核对 501/501 Parent 引用有效；Coverage 501 行、2004 单元格 | 返回 SKU `00532931`、price 596、stock 403 与原始行，来源 `b1-sku-bom.csv`；同隔离 KB 的基础 CSV 含同 SKU，结果位于 Top 3 | PASS |
| `sku_gbk.csv` | upload `8a8ebb78f3ab`；doc `44116743a1`；IndexRun `522a5d8e-3a3f-408f-8b16-03d30c0a443b` | Active；502 Chunks（501 Leaf / 1 Parent）；501 个 Leaf Parent 引用有效 | 返回 SKU `00532931`、price 595、stock 372，来源 `b1-sku-gbk.csv`，排名 1 | PASS |
| `multi_sheet.xlsx`（容器注释变化的同内容副本） | upload `7525be2cb3e7`；doc `fefcb6ae7a`；upload IndexRun `c77dd55e-6600-4f85-83c2-12f1f0b863de`；最终重索引任务 `d0611baa-c0e1-4cd6-a55a-17cc561f2c9b`、IndexRun `a4406804-add0-469a-a87d-cf195ab61255`、Trace `b252da2f7269` | 最终 93 Chunks（89 Leaf / 4 Parent），向量 DB 无缺失或无效 Parent 引用；Leaf/Parent 最大 token 63/47；Coverage 4/4 源表节点、89 行、320 非空单元格，`sampled=false`、rate 1.0；Quality Record 100 分、无异常；最终 Active | `E0002`、`E0003` 检索返回员工行、来源和 Sheet；`汇总 =B3-C3` 命中公式行，来源 Sheet 为“汇总”。Chunks API 中保留 `=B3-C3` 至 `=B6-C6` | PASS |
| `staff_regulation_scnu.pdf`（真实 API 上传） | upload `b96f4d86ebe3`；doc `efac316c4b`；SHA256 `aa819099435b2afc6b0b40d484607384de201c6e0ad37de489040dd69d4f0968` | API 接收；PDF Parser 和 LegalChunkStrategy 成功；Quality Record 记录 nodes 58 / chunks 28 / anomalies 0。随后 Worker 子进程 SIGKILL，未取得最终 Registry/IndexRun/Search 结果 | 无最终 Search 证据 | BLOCKED |

Excel 样本原始 SHA256 因同内容旧测试记录触发重复审核；为复用隔离 KB 并实际运行新解析代码，保留 XLSX 内部工作簿条目，仅更改 ZIP 容器注释后上传。候选曾因隔离库内已软删除旧记录的 MinHash 重复提示进入 pending_review；检查质量记录及索引内容后，使用既有 pending 审核 API 放行，之后重索引以验证最终代码，未绕过 Coverage/QualityGate。旧测试文档只在隔离 KB 删除和清理。

## Parser 与 Coverage 补充证据

- `multi_sheet.xlsx` 直接解析：93 Chunks、89 Leaf / 4 Parent；Parent 引用有效；最新审计 89 行/320 单元格、4/4 源表节点、非采样全覆盖。
- `report.pdf` 直接解析：3904 Chunks（3140 Leaf / 764 Parent），Parent 引用有效，最大 Leaf 500、Parent 2000 tokens；753/753 源 Leaf 和表格 2416 行/7837 单元格未报缺口。但审计标记 `sampled=true` 且 `coverage_rate=null`，因此只记录采样审计未见缺口，不宣称全量 100%。
- `doc_cn.pdf` 与 `contract_gf2017_0201_cn.pdf` 直接 Parser/Chunk/Coverage 运行成功，Parent 有效且 Token 上限符合配置；真实 API→Index→Search 未全部完成，不能记作端到端 PASS。
- 501 页英文 PDF 的有意义拉丁文本过滤行为做了解析/过滤层核验；没有真实上传 Search 证据。
- 扫描件 OCR 依赖、Worker SIGKILL 根因、任务失败恢复、PostgreSQL 迁移漂移属于 B2/B3 或完整 E2E 的未完成项，不在本 PR 扩大处理。
- 当前网络不能加载 tiktoken 词表，运行时沿用字符估算回退。Chunk 实测值符合配置边界；实际 cl100k BPE Token 数为 **UNVERIFIED**。

## 测试与状态

| 检查 | 结果 |
|---|---|
| T0：修改文件 `py_compile` | PASS |
| `git diff --check` | PASS（只有 Git 提示部分工作树 CRLF 将规范化为 LF） |
| 定向回归：`test_batch_b1_content_integrity.py`、`test_chunk_type_contract.py`、`test_pr55_coverage_quality_gate.py`、`test_excel_parser.py`、`test_pdf_parser.py`、`test_legal_chunking.py` | PASS：65 passed |
| CSV API→Worker→Index→Search（三样本） | PASS |
| Excel API→Worker→Index→Search | PASS |
| PDF Parser/Chunk 定向检查 | PASS |
| PDF API→Worker→Index→Search | BLOCKED：Worker 子进程 SIGKILL 后无最终状态 |
| cl100k 精确 Token 数 | UNVERIFIED：词表网络下载失败 |
| 全量 18 类文档矩阵 / RAG 全局 E2E | 尚未执行；保持 `RAG_UPLOAD_E2E_VERIFIED=false` |

## 交付边界与风险

- 本报告只覆盖 B1。Worker SIGKILL、资源峰值/恢复和失败终态归 B2 定位；OCR 依赖、迁移和其他元数据缺项归 B3 判断。
- 不运行付费 Provider、不修改 Clash/代理、不触碰正式数据库、不修改 #55/#58 已合并提交、不合并 main。
- PDF真实索引检索阻塞仍为 B1 合并风险。本 PR 应保持 Draft/不可合并状态，待按授权的后续任务解决 Worker 运行阻塞并完成 PDF 真实验收后再重新判断。
- 不修改 `RAG_UPLOAD_E2E_VERIFIED`；当前值继续为 `false`。
