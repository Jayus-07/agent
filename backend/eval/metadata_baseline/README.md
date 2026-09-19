# 元数据基线评估（规划阶段 1.2 / 1.3 / 5.1 脚手架）

> 规划：`docs/2026-09-19-RAG元数据管道统一抽取与级联路由上线规划.md`
> 状态：**脚手架就绪，黄金集数据待标注**（外部阻断，§7.2）。标注完成后本目录
> 产出《规则链基线报告》与《统一抽取基线报告》，作为上线门禁（+5pp）的测量依据。

## 黄金集 JSONL 格式（golden.jsonl）

每行一条，字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 稳定样本 id（建议 `doc_type-序号`，如 `legal-017`） |
| `text` | ✅ | 文档全文或与线上一致的采样（≤6000 字，对齐 METADATA_LLM_EXTRACT_MAX_CHARS） |
| `doc_type_gold` | ✅ | 双人标注仲裁后的类型（14 类枚举，与 metadata_schema.DOC_TYPES 一致） |
| `domain_gold` |  | 业务域（11 值，与 DOMAINS 一致；缺省 general） |
| `risk_gold` |  | 风险正例标记：`["依据词句", ...]` 或 `true`；负例省略/false |
| `filename` / `file_path` |  | L0 强先验用；标注时保留真实文件名（可脱敏路径） |
| `annotators` |  | `["标注员A", "标注员B"]`，Kappa 统计用 |
| `dispute` |  | 分歧样本仲裁记录（规划 §1.1：争议 100% 仲裁） |

样例见 `golden_sample.jsonl`（仅格式演示，不参与统计）。

验收口径（规划 §1.2）：每类 ≥ 50 条、总 ≥ 1200 条、双人 Kappa ≥ 0.80。

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

运行环境：仓库根、项目 venv 解释器。级联评估必须能加载 Embedding；不可用时命令
显式失败，不会伪造 R1 结果。发布门禁要求每个类型至少 50 条黄金样本，并要求
正式黄金集每条样本还必须带双人 `annotators`（至少 2 人）或非空的
`adjudication`/`dispute` 仲裁记录；仅有标签数量不算已仲裁支持。预测文件携带
taxonomy/rules/model/prompt 四类版本指纹；`--load-report` 和
`--rollback-report` 是 staging 压测/演练的独立证据，不应把聚合指标复制到每条预测行。
缺少任一证据时门禁 fail-closed。

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

阶段 5.1 影子采集可直接落成本预测格式（`id/text/pred`），用
`evaluate --pred shadow_dump.jsonl` 复用同一指标实现，避免两套口径。现网一致率
仅作诊断；准确率、coverage、abstain、ECE、路径级 precision 和 load/rollback
证据才是放量依据。
