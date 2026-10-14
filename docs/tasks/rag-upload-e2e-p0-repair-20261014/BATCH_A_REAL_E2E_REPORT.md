# PR #58 Batch A 真实上传验收报告

日期：2026-10-14
Worktree：`C:\Users\wh\.codex\worktrees\rag-upload-p0-batch-a\agent`
分支：`codex/rag-upload-e2e-p0-20261014`
隔离环境：Compose project `rag-pr55-e2e`，KB `rag_eval_kb`。上传经 APISIX 进入真实 API，由 Celery Worker 处理；对账读取隔离 PostgreSQL、BM25 与 Search API。未访问正式 KB，未调用付费 Provider，未改动 Clash/代理设置。

## 场景结果

| 场景 | 真实证据 | 结果 |
| --- | --- | --- |
| FAQ 同名更新 | upload `824b42a04665`；doc `29555be9a7`；Active v2 / generation `52134f9452b24575`。Registry 41 个物理 chunk ID 与 PG 向量逐一对应：40 Leaf、1 Parent，40 个非空 Parent 引用全部有效。质量记录 Coverage 80/80、非抽样。Search API HTTP 200，首条结果保留“年假怎么申请”的问题与正确 OA 答案。 | PASS |
| `sku.csv` 零有效 Leaf | upload `1734dd899dee`；IndexRun `failed/settle`。实际输出为 0 Leaf、1 Parent；Coverage 为 0/1、覆盖率 0、记录 `chunks_present` 与 `coverage_gap`。没有 Active registry 行，也没有该 doc 的正式向量。21/22 个 Leaf 因 `low_chinese_ratio` 或 `too_short` 被过滤，作为 Batch B 的解析策略问题保留。 | PASS（按预期失败） |
| PII 脱敏与正文覆盖 | upload `3907d7b449cc`；doc `f4356bd37f`；Active v3 / generation `e8de46a48a9249cd`。PG 与 BM25 均只含当前代 2 个 chunk ID；原手机号命中数 0、脱敏值命中数 1。质量记录 Coverage 4/4、`sampled=false`、`pii_mask_normalized=true`。Search API HTTP 200 返回脱敏后的正文。`test_financial_index_filter_masks_pii_and_still_detects_missing_body` 证明删掉非脱敏正文触发 `coverage_gap`。 | PASS |
| 同名更新失败保护 | upload `fae38ed41109`；候选 IndexRun `failed/settle`，质量记录 Coverage 0/1 并包含 `chunks_present`、`coverage_gap`。原 `help_center_mixed.md` doc `52a4bacfad` 的 hash、Active generation `4a0813dee6e64d92`、v1、8 个 chunk 均未改变。失败后 Search API HTTP 200 仍召回原 Active 内容。 | PASS |

## 发现与修复

真实 PII 首次上传发现两处 Batch A 缺陷：过滤阶段未将路由判定的 `doc_type` 传给 PII 规则，原手机号进入 Leaf 和文档级向量；后续替换虽然清掉旧 PG 向量，候选 BM25 重建却没有排除被替换的旧物理 chunk ID，Search 仍能命中旧代原文。

最小修复复用现有 `ChunkFilter` 脱敏逻辑，并在候选入口将 Active registry 的 `chunk_ids` 传给 Indexer 已有 `exclude_ids` 参数。修复后重新上传 PII v3，PG、BM25、Registry 与 Search 均只见当前脱敏代；未新增索引机制。

## 回归与边界

- 定向回归：34 passed，覆盖 Indexer BM25 同步、候选发布、BM25 排除、Batch A QualityGate 与 PII/正文缺失测试。
- Python `py_compile`、`git diff --check` 通过。
- 主机已配置 pytest；Compose 应用镜像未安装 pytest，故定向测试在主机 `D:\Python\python.exe` 执行。服务容器只读挂载使容器内 `py_compile` 无法写入 `__pycache__`；主机语法检查通过。
- 本次没有调用付费 Provider。Metadata 模型本地连接失败后走规则回退，真实索引发布与 Search 仍成功。
- `sku.csv` 的低中文比例过滤不在本次修复范围，留给 Batch B；PDF、Excel 与长 TXT 资源治理及完整 18 类 E2E 仍未完成。
- `RAG_UPLOAD_E2E_VERIFIED` 保持 `false`；本报告仅验收 Batch A 场景。

## GitHub PR 验证

- 代码与本报告首个提交：`eda88fb15975f7755ecc6cc61c4fef75359356d0`。
- 该提交的 `secret-scan` 与 `RAG PR Smoke` 均成功；免费 preflight、定向回归通过。8-case real-API/付费 Provider 评测按门禁跳过。
- PR #58 保持 Open / Mergeable；已有 2 条 Codex Review Thread 均为 resolved。
