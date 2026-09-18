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
python -m backend.eval.metadata_baseline.predict --golden ... --out ... --cascade

# 3) 评估 + JSON 报告
python -m backend.eval.metadata_baseline.evaluate \
    --golden backend/eval/metadata_baseline/golden.jsonl \
    --pred backend/eval/metadata_baseline/preds_unified.jsonl \
    --json report_unified.json
```

运行环境：仓库根、项目 venv 解释器；规则链评估需 DB/Redis 可用
（动态词库热加载），统一抽取需 LLM proxy 可用。

## 指标口径

- `doc_type` / `business_domain`：per-label P/R/F1 + macro-F1 + accuracy + 非对角混淆对 top8
- 风险召回：`risk_gold` 非空为正例，`pred.risk.level != none` 判命中（Schema v1 risk 字段）
- 延迟：预测携带 `latency_ms` 时输出 P50/P95（影子模式同口径，可跨报告对比）

## 与影子模式的关系

阶段 5.1 影子采集可直接落成本预测格式（`id/text/pred`），用
`evaluate --pred shadow_dump.jsonl` 复用同一指标实现，避免两套口径。
