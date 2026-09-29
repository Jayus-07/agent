# RAG 测试知识库统一设计

## 1. 背景与目标

当前存在两套相互独立的测试资产：

- `rag_test_kb`：小型回归语料，服务日常快速测试。
- `rag_100_docs`：100 份异构文档及其专项标注，服务格式覆盖、质量评测和容量探路。

两套资产的 KB ID、文档目录、评测数据入口不完全一致，容易出现“评测集加载了，但对应文档没有入库”或“跑了小库却误以为验证了 100 份语料”的问题。

目标：

1. 统一测试知识库的管理入口和命名。
2. 保留 baseline 与 expanded_100 的可重复、可隔离评测口径。
3. 测试数据与生产知识库物理/权限隔离。
4. 为未来 20k 容量测试预留同一套元数据和评测接口。
5. 旧 KB 在迁移期保持可用，避免一次性破坏现有测试和索引。

## 2. 设计原则

- 统一管理，不无标签混合检索。
- `audience=test` 是测试数据的安全边界，禁止进入对客兜底路径。
- 评测集显式选择 corpus/suite，不依赖默认路径猜测。
- baseline 用于 PR 快速回归，expanded_100 用于发布/夜间评测，二者指标不得混算。
- 20k 只在独立 staging 容量环境运行，不把大规模数据提交进仓库。
- 迁移脚本幂等，可重复执行，失败不改变生产知识库。

## 3. 目标模型

统一测试 KB：

```text
kb_id = rag_eval_kb
audience = test
```

每个文档增加测试语料标签：

```text
fixture_set = baseline | expanded_100 | scale_20k
```

建议补充：

```text
fixture_doc_id   # 测试资产稳定 ID
fixture_version  # 语料版本
```

`fixture_set` 只用于评测范围和诊断，不替代 `kb_id`、`department`、`permission_scope`、版本治理等生产字段。

## 4. 目录和评测集

目标目录：

```text
backend/evaluation/fixtures/rag_eval_kb/
├── baseline/files/
├── expanded_100/files/
└── manifest.json
```

评测入口统一在：

```text
backend/evaluation/datasets/rag/
├── cases.jsonl
└── suites/
    ├── pr_baseline.json
    ├── expanded_100.json
    ├── quick_26.json
    └── scale_20k.json
```

`cases.jsonl` 保持 canonical case 的单一事实源；suite 只保存 case ID、语料范围、运行档位和阈值引用，不复制完整用例。

迁移期保留：

- `backend/evaluation/fixtures/rag_100_docs/` 作为旧路径兼容入口。
- `rag_test_kb`、`rag_100_docs` 作为旧 KB 别名或只读兼容配置。
- 旧评测 JSON 可继续读取，但新测试不再新增到旧格式。

## 5. 运行口径

### PR/日常开发

只跑 baseline 语料和 quick suite，目标是快速发现解析、分块、索引、过滤和检索回归。

### 发布/夜间

跑 expanded_100，覆盖多格式、复杂 PDF、扫描件、多 Sheet Excel、CSV 编码、版本、权限和跨文档问题；生产链路打开 MultiQuery 时，评测必须显式打开同一开关。

### 20k 容量

在独立 staging 环境导入 `scale_20k`，记录文档数、chunk 数、索引耗时、Embedding 吞吐、内存、PG 连接、队列积压、P50/P95/P99 延迟、Recall@K、MRR、Top-1 和错误率。

## 6. 入库流程

统一入库脚本执行以下步骤：

1. 读取 manifest，校验文件存在、格式、稳定 ID 和标注完整性。
2. 将文档复制到测试语料目标目录，并写入 `kb_id=rag_eval_kb`、`fixture_set` 等元数据。
3. 通过现有解析、分块、Embedding、向量库、BM25 和 registry 全链路入库。
4. 对失败文档保留失败状态和原因，不把半成品标为 active。
5. 完成向量、chunk、BM25、registry 一致性检查。
6. 扫描件只有 OCR 可用时才允许进入 expanded_100。

删除或重建只能作用于 `audience=test` 的 KB，禁止脚本通过通配符触碰生产 KB。

## 7. 兼容与切换

切换顺序：

1. 新增 `rag_eval_kb` 配置和 suite/schema 支持。
2. 迁移 baseline 与 expanded_100 文档，分别打 `fixture_set`。
3. 用新入口跑两套评测，与旧 KB 结果对比。
4. 新测试默认改用 `rag_eval_kb`。
5. 旧 KB 保留一个发布周期，只读兼容。
6. 确认无调用方后再清理旧 KB 和旧目录。

不迁移生产文档，不修改生产 KB 的检索范围，不删除现有测试索引。

## 8. 验收标准

- baseline 和 expanded_100 均可通过统一入口入库和清理。
- suite 显式选择后，评测集与 KB/corpus 范围一致。
- 评测输出明确记录 `kb_id`、`fixture_set`、数据版本和检索链开关。
- 旧命令在迁移期仍能运行，并输出兼容提示。
- 测试 KB 不会被员工/对客授权集合返回。
- 至少覆盖：100 份全量检索、扫描件 OCR 开关、权限/版本隔离、重复入库幂等、删除一致性。
- 所有新增代码、脚本和迁移步骤包含中文备注，说明为什么保留兼容层及其删除条件。

