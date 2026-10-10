# RAG 元数据基线评估

本目录对规则分类、统一抽取和级联路由做离线评估。分类契约见
[`docs/domains/rag.md`](../../../docs/domains/rag.md#上传元数据与级联决策)；
样本和结果文件以当前数据与代码为准，不在 README 固定数据规模或通过率。

## 黄金集 JSONL 格式（golden.jsonl）

每行一条，字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 稳定样本 id（建议 `doc_type-序号`，如 `legal-017`） |
| `text` | ✅ | 文档全文或与线上一致的采样；采样口径以配置为准 |
| `doc_type_gold` | ✅ | 标注类型，与 `metadata_schema.DOC_TYPES` 一致 |
| `domain_gold` |  | 业务域，与 `metadata_schema.DOMAINS` 一致；缺省 `general` |
| `risk_gold` |  | 风险正例标记：`["依据词句", ...]` 或 `true`；负例省略/false |
| `filename` / `file_path` |  | L0 强先验用；标注时保留真实文件名（可脱敏路径） |
| `annotators` |  | `["标注员A", "标注员B"]`，Kappa 统计用 |
| `dispute` |  | 分歧样本仲裁记录 |

样例见 `golden_sample.jsonl`（仅格式演示，不参与统计）。

## 用法

```bash
# 1) 规则链基线（现场推理；胶着样本触发既有 LLM 仲裁 = 规则链真实成本）
python -m backend.eval.metadata_baseline.evaluate \
    --golden backend/eval/metadata_baseline/golden.jsonl --pred rule

# 2) 统一抽取 / 级联路由：先离线生成预测（一次 LLM 成本，可反复回放）
python -m backend.eval.metadata_baseline.predict \
    --golden backend/eval/metadata_baseline/golden.jsonl \
    --out backend/eval/metadata_baseline/preds_unified.jsonl
python -m backend.eval.metadata_baseline.predict \
    --golden backend/eval/metadata_baseline/golden.jsonl \
    --out backend/eval/metadata_baseline/preds_cascade.jsonl \
    --route cascade

# 3) 评估 + JSON 报告
python -m backend.eval.metadata_baseline.evaluate \
    --golden backend/eval/metadata_baseline/golden.jsonl \
    --pred backend/eval/metadata_baseline/preds_unified.jsonl \
    --json report_unified.json

# 4) 发布门禁（即使 --allow-dry-run 也不会绕过门禁）
python -m backend.eval.metadata_baseline.validate_release \
    --golden backend/eval/metadata_baseline/golden.jsonl \
    --pred backend/eval/metadata_baseline/preds_unified.jsonl \
    --load-report staging/metadata-load-report.json \
    --rollback-report staging/metadata-rollback-report.json \
    --allow-dry-run \
    --report metadata_release_report.json
```

运行环境：仓库根、项目 venv 解释器。级联评估依赖 Embedding；不可用时命令显式失败。
发布门禁所需的样本覆盖、标注依据和压测/回滚证据由 `validate_release` 校验，
以其当前规则和测试为准。预测会携带 taxonomy、rules、model、prompt 版本指纹。

`metadata-load-v1` 报告必须包含：

```json
{
  "report_version": "metadata-load-v1",
  "peak_multiplier": 2.0,
  "sustained_queue_growth": false,
  "queue_age_p95_seconds": 2.4,
  "primary_p95_ms": 420.0,
  "shadow_p95_ms": 390.0,
  "embedding_qps": 18.0,
  "llm_qps": 6.0,
  "llm_429_rate": 0.0,
  "db_pool_wait_p95_ms": 12.0,
  "duplicate_write_count": 0
}
```

`metadata-rollback-v1` 报告必须包含 `rollback_duration_seconds`、
`old_fingerprint_present`、`idempotent_replay` 和 `duplicate_write_count`；门禁要求
回滚不超过 600 秒、旧指纹存在、幂等重放成功且重复写入为 0。报告版本不匹配、
字段缺失、峰值未达到 2× 或 LLM 429/重复写入出现，均直接阻断发布。

## 指标口径

- `doc_type` / `business_domain`：per-label P/R/F1 + macro-F1 + accuracy + 非对角混淆对 top8
- 风险召回：`risk_gold` 非空为正例，`pred.risk.level != none` 判命中（Schema v1 risk 字段）
- 延迟：预测携带 `latency_ms` 时输出 P50/P95（影子模式同口径，可跨报告对比）

## 与影子模式的关系

影子采集结果可转换为 `id/text/pred` 格式，并通过 `evaluate --pred` 复用离线指标口径。
一致率仅作诊断；发布结论须结合准确率、coverage、abstain、ECE、路径级 precision
及 load/rollback 证据。
