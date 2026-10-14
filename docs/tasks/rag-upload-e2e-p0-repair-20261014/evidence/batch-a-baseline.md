# Batch A 基线证据（PR #55 合并 HEAD）

采集时间：2026-10-14（Asia/Shanghai）

代码 HEAD：`cfa482e0bce5b1979f82304cde43bdf7d20dac5e`
隔离测试 KB：`rag_eval_kb`。所有 SQL 均在先前测试数据库的只读副本中执行；未访问或修改正式业务数据库。

## FAQ 数据库证据

上传 `549c7be26ff8`，文档 `29555be9a7`，IndexRun `2a917af5-a479-4c64-852f-79f783a26358`，Trace `9d61b9d59e34`。

只读 SQL 汇总结果：

```text
rag_vectors WHERE doc_id='29555be9a7'
  granularity=leaf: 40
  granularity=parent: 0
  leaf 行中无法解析到同文档 granularity=parent 的 parent_chunk_id: 40

doc_registry
  status=active
  chunk_count=40
  quality_status=pass
  quality_issues=内容重复率偏高, filtered_chunks:1(low_chinese_ratio=1)
```

向量表中的规范 `chunk_id` 位于 metadata JSON；物理向量 ID 带 `chroma::cand:` 前缀，不应拿物理 ID 与 Leaf 的 canonical `parent_chunk_id` 直接比较。查询按同一 `doc_id` 下 metadata `chunk_id` 与 `parent_chunk_id` 对账，并要求目标 granularity 为 `parent`。

## CSV IndexRun 证据

```text
upload_id    status  stage   error
2349c6976cf6 failed  settle  2349c6976cf6.9cdbb601ccba4f50.csv produced 0 chunks (ext=.csv, parsed=22)
9cd01a6dfc76 failed  settle  9cd01a6dfc76.8c54e177ba6b47f3.csv produced 0 chunks (ext=.csv, parsed=22)
305ce3bd5688 failed  settle  305ce3bd5688.5df32db0a52f4c6e.csv produced 0 chunks (ext=.csv, parsed=22)
```

原始 E2E 报告记录上述 CSV 的质量记录把 501 行、2004 单元格记为完整覆盖；真实最终输出为 0 Chunk，说明覆盖审计使用了过滤前候选。

## FAQ 基线源码复现

运行于基线代码：

```text
DocxParser: 局部识别 40 个 Q/A 对
doc_type=faq → QAChunkStrategy
question_nodes=40, nonempty_questions=0
raw_chunks=41 (leaf=40, parent=1)
ChunkFilter: filtered_chunks=1, kept_chunks=40
最终 parent 数=0, Leaf 声明 parent 引用=40, 无效引用=40
parse 阶段 Coverage: leaf_total=40, covered=40, uncovered_count=0, coverage_rate=1.0
```

测试样本：`C:\Users\wh\Desktop\不同文件测试文档\testset\03_faq_only\faq_only.docx`；原始问句与内容样例见未改写的 `../rag-upload-postmerge-e2e-20261014/REPORT.md`。问题节点有原文 `raw_text`，但清洗后 `text=''`，清洗留痕为 `dropped_isolated_captions(1)`。后续 Parent 因只含 `Q：` 行被过滤，Index ID 映射仅重写仍存在 Parent 的 ID，因此未发现失效引用。

## 原始验收报告

合并后真实 API/DB/检索测试的原始完整报告已原文保存在 `docs/tasks/rag-upload-postmerge-e2e-20261014/REPORT.md`。报告 SHA256：`4CC0324E099F1110A2D5DE93FBDA114D62E03612A868FD08145D7D01FF63CE97`。
