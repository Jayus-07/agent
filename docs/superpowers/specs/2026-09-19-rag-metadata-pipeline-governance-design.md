# RAG 元数据管道治理与高并发抽取设计

> 日期：2026-09-19
> 状态：设计稿，待用户复核
> 关联规划：[2026-09-19-RAG元数据管道统一抽取与级联路由上线规划.md](../../2026-09-19-RAG元数据管道统一抽取与级联路由上线规划.md)

## 1. 设计结论

采用“证据规则 + 校准分类器 + 单次 LLM 结构化抽取 + 保守兜底 + 人审闭环”的混合架构。

核心决策：

- 规则只负责证据提取、硬约束和安全兜底，不再承担复杂语义分类。
- 文件名、目录和宽泛关键词只能作为弱特征；只有经过黄金集验证的高精度强信号才允许直接定案。
- 分类器必须输出可校准置信度，并支持 `abstain`；原始 cosine 相似度不能直接当置信度。
- LLM 保留一个统一结构化抽取入口；规则仲裁、低置信复验和关键词 LLM 不得隐藏在 fallback 内部。
- LLM、Embedding、数据库写入均采用有界并发；上传接口任务化，索引走 `rag_index` 队列。
- 影子评估独立异步执行，不得阻塞主索引路径；切流门禁以黄金标签和人工复核为准，现网 LLM 一致率只作诊断指标。

在新链路完成正式评估前，`METADATA_CASCADE_ENABLED` 保持关闭。

## 2. 目标与非目标

### 2.1 目标

1. 降低 `doc_type`、`business_domain` 和 `risk` 的误判率。
2. 让规则变更可审计、可评估、可回滚，不再靠新增正则修个案。
3. 让高置信、低风险样本走低成本路径，复杂样本才调用 LLM。
4. 支持高并发上传与索引，避免远程模型、线程池或数据库连接被打满。
5. 保留现有下游字段兼容性，允许分阶段迁移和快速回滚。

### 2.2 非目标

- 本设计不新增业务 `doc_type`。
- 本设计不重写上传 API 和检索协议。
- 本设计不训练或微调大模型。
- 本设计不在没有黄金集和压力测试的情况下打开级联直通。

## 3. 总体架构

```text
上传请求
  │
  ├─ 写入任务表，返回 task_id
  │
  └─ rag_index 队列
       │
       ▼
  文本解析与归一化
       │
       ▼
  EvidenceExtractor（纯规则、无最终决策）
       │
       ▼
  DecisionRouter
       ├─ R0 高精度硬规则：无冲突才直接接受
       ├─ R1 校准分类器：高置信才接受，否则 abstain
       └─ R2 统一 LLM Schema 抽取
       │
       ▼
  SchemaValidator + PolicyGate
       ├─ 合法且高置信 → 持久化
       ├─ 冲突/低置信/高风险 → pending_review
       └─ LLM 失败 → 保守 fallback + pending_review（必要时）
       │
       ▼
  元数据、版本、证据、指标写入
```

主路径与影子路径分离：

```text
主索引：R0 → R1 → R2 → fallback
影子评估：独立队列异步运行 → 与黄金标签/人工结果比较
```

## 4. 组件职责

### 4.1 TaxonomySpec：唯一事实源

建立一份版本化 TaxonomySpec，统一定义：

- `doc_type`、`business_domain`、`risk_level` 枚举；
- 每个类型的描述、别名、边界说明和反例；
- 允许的动态规则目标；
- 评估集标签和 Prompt 枚举文本；
- 每类的自动接受阈值和人工审核策略。

Schema、Prompt、分类器标签、路由描述和动态词库校验均从 TaxonomySpec 派生，禁止重复手写第二份枚举。

### 4.2 EvidenceExtractor：规则证据层

输入文档文本、文件名和路径，输出标准化证据：

```json
{
  "signals": [
    {"rule_id": "legal_clause", "value": "第十二条", "strength": "strong"}
  ],
  "candidates": ["legal", "contract_template"],
  "conflicts": ["legal_vs_contract_template"],
  "features": {"heading_count": 8, "table_rows": 0}
}
```

约束：

- 不调用 LLM。
- 不返回最终 `doc_type`。
- 每条证据必须带 `rule_id` 和版本。
- 文件名/路径默认是弱特征；只有显式高精度 allowlist 才能进入 R0。
- 规则冲突时输出候选和冲突，不强行选第一名。

### 4.3 R0：高精度硬规则

R0 只处理可证明的强信号，例如明确的系统生成标识、固定格式编号、受控目录和互不冲突的显式标签。

R0 直接接受必须同时满足：

- 规则属于已批准的高精度 allowlist；
- 至少一个强信号，且没有冲突信号；
- 在独立验证集上达到直接定案 precision 门槛；
- Taxonomy、规则和策略版本均可追溯。

普通“合同”“制度”“流程”“财务”等词不能单独触发 R0。

### 4.4 R1：校准分类器

R1 使用文本 embedding、结构特征、规则证据和弱文件名特征训练分类器。分类器必须：

- 训练、验证、测试按文档来源或文档族拆分；
- 记录 embedding、特征和 Taxonomy 版本；
- 进行概率校准；
- 支持每类别阈值；
- 输出 top-k 候选和 abstain；
- 模型不可用或特征版本不匹配时自动跳过，不阻塞主流程。

R1 只有在满足该类别 precision 门槛时才自动接受，否则进入 R2。

### 4.5 R2：统一 LLM 结构化抽取

LLM 单次返回：

- `doc_type`；
- `business_domain`；
- `business_domain_candidates`；
- `summary`；
- `keywords`；
- `entities`；
- `risk`；
- `time_refs`；
- `confidence`。

LLM 结果必须经过同一 Pydantic Schema 和 PolicyGate 校验。非法枚举、异常置信度、越界字段和风险字段缺失均进入失败或复核流程。

### 4.6 Fallback：保守确定性兜底

Fallback 禁止再次调用 LLM。处理规则：

| 情况 | 结果 |
|---|---|
| 存在无冲突高精度证据 | 使用证据对应类型 |
| 候选冲突 | `abstain`，进入 `pending_review` |
| 证据不足 | `general` + 低置信度 |
| 高风险字段无法确认 | 保守标记风险并进入复核 |

Fallback 返回完整元数据契约，不允许异常路径只返回一个 `doc_type`。

## 5. 统一决策契约

所有路径最终输出同一个 `DecisionEnvelope`：

```json
{
  "decision": "accepted|abstain|fallback|review",
  "doc_type": "legal",
  "confidence": 0.91,
  "candidates": [
    {"label": "legal", "score": 0.91},
    {"label": "contract_template", "score": 0.63}
  ],
  "source": "r0|r1|llm|fallback|human",
  "evidence": [],
  "conflicts": [],
  "taxonomy_version": "v1",
  "rules_version": "r2026-09-19",
  "model_version": "metadata-lr-001",
  "prompt_version": 2
}
```

`doc_type` 和 `business_domain` 分离：业务域允许主域加候选域，避免跨域文档被强行压成一个标签；`risk` 独立判定，不从 `doc_type` 推断。

## 6. 规则治理

规则改动走以下流程：

```text
新增/修改规则
  → 规则审查
  → 黄金集 + 反例集回归
  → 生成 precision/coverage/误伤报告
  → 审批
  → 生成规则版本和 hash
  → 灰度生效
```

动态词库必须满足：

- `doc_type` 必须来自 TaxonomySpec；
- 权重、长度和命中范围有边界；
- 记录操作者、原因、审批单和生效时间；
- 采用不可变版本快照；
- 规则快照 hash 纳入 `metadata_fingerprint`；
- 可一键回滚到上一版本；
- 修改后自动触发离线回归。

静态规则不再通过直接编辑 Python 代码扩展。现有规则先作为 legacy fallback 迁移，迁移完成前不删除。

## 7. 高并发设计

### 7.1 任务化入口

上传接口只完成文件登记和任务创建，返回 `task_id`。解析、抽取、Embedding、写库均在 `rag_index` 队列中执行，不占用 API 请求线程。

### 7.2 有界并发池

为不同外部资源设置独立并发控制：

- 解析池：按 CPU 核数设置；
- Embedding 池：按供应商 QPS 和批量大小设置；
- LLM 池：按供应商 QPS、token 预算和租户配额设置；
- 数据库池：按 PostgreSQL 连接池上限设置；
- 影子池：独立于主索引，主队列拥塞时自动暂停。

禁止无限制 `asyncio.gather` 和无上限线程池扩张。

### 7.3 缓存与幂等

- 文本 hash + 模型版本作为抽取缓存键；
- 文档 hash + Embedding 版本作为向量缓存键；
- Taxonomy 描述向量预计算并按版本缓存；
- 同一 `task_id`、文件 hash 和版本组合只允许一次有效写入；
- 失败重试必须使用幂等键和指数退避。

### 7.4 背压与故障隔离

- 队列达到上限时返回排队或限流状态；
- 按租户限制在途任务数；
- Embedding/LLM 429 进入退避，不立即重试；
- 连续失败触发熔断，转入 fallback 或人工队列；
- 单文档超时不影响同批其他文档；
- 影子任务失败不得影响主任务。

### 7.5 容量估算

系统吞吐受以下最小值限制：

```text
有效吞吐 = min(
  worker 处理能力,
  Embedding QPS / 每文档调用次数,
  LLM QPS / 每文档调用次数,
  数据库写入能力,
  租户配额
)
```

评估时必须分别测量规则命中率、分类器覆盖率、LLM 进入率和每阶段等待时间，不能只看端到端平均耗时。

## 8. 评估与上线门禁

### 8.1 数据集

黄金集总量目标为 1200 条以上；每个 `doc_type` 至少 50 条，额外补充跨域、冲突、模糊命名、表格、OCR 和长文档样本。1200 是覆盖目标，不是 14 类乘 50 的算术结果。

评估集必须包含：

- 文件名信号消融；
- 正文信号和完整信号两套结果；
- 训练、验证、测试按文档来源拆分；
- 双人标注、争议仲裁和 Kappa；
- 高风险样本和明确负例。

### 8.2 指标

每条路径分别统计：

- precision、recall、macro-F1；
- coverage、abstain rate；
- 每类别混淆矩阵；
- 置信度校准误差；
- 风险字段召回率；
- P50/P95/P99 延迟；
- LLM 调用率、成本和 429 率；
- fallback、review 和重试比例。

现网 LLM 与新链路的一致率只用于发现差异，不能作为准确率门禁。

### 8.3 推荐门禁

- R0 直接定案：独立验证集 precision ≥ 99.5%；
- R1 自动接受：每类别 precision ≥ 98%，否则 abstain；
- R2 Schema 校验通过率 ≥ 99.5%；
- fallback 路径 LLM 调用数为 0；
- 高风险字段召回率 ≥ 95%；
- 影子路径不增加主路径 P95；
- 压测达到预估峰值 2 倍时，队列、LLM、Embedding 和数据库均无持续增长；
- 回滚在 10 分钟内完成，且不产生重复写入。

## 9. 可观测性

每条元数据结果记录以下维度：

- `route_source`；
- `decision`；
- `confidence`；
- `taxonomy_version`；
- `rules_version`；
- `model_version`；
- `prompt_version`；
- `queue_wait_ms`、`stage_latency_ms`；
- `retry_count`、`fallback_reason`；
- `review_status`。

Prometheus/Grafana 至少提供：

- 各路径 coverage、precision 和 abstain；
- 队列长度、年龄和吞吐；
- Embedding/LLM 并发、QPS、429、超时；
- 数据库连接池等待；
- 每租户在途任务；
- 规则版本切换和回滚事件。

## 10. 分阶段迁移

### 阶段 0：契约治理

完成 TaxonomySpec、DecisionEnvelope、Schema 枚举校验和版本指纹；级联保持关闭。

### 阶段 1：统一主路径

保留统一 LLM 抽取作为主路径，移除 fallback 内部的隐式 LLM；补齐完整确定性 fallback。

### 阶段 2：评估集与规则迁移

建立正式黄金集、反例集和规则回归报告；将现有规则迁移为 EvidenceExtractor 数据；动态词库接入审批和版本化。

### 阶段 3：分类器离线验证

训练、校准和评估 R1；未满足逐类 precision 门禁时不接入在线自动接受。

### 阶段 4：影子与压测

影子走独立队列，完成至少 7 天生产样本和 2 倍峰值压测；主路径 P95 不得被影子拖慢。

### 阶段 5：灰度

按 1% → 10% → 50% → 100% 灰度，每阶段检查准确率、成本、延迟、队列和错误率；任何门禁失败立即切回统一 LLM 主路径。

### 阶段 6：长期治理

每月评估和漂移检测；人审结果进入标注库；规则、分类器、Prompt 和 Taxonomy 均按版本独立回滚。

## 11. 与现有实现的迁移映射

| 当前实现 | 目标职责 |
|---|---|
| `domain_data.py` | 迁移为受治理的规则证据数据，保留 legacy fallback |
| `metadata.py::classify_with_confidence` | 拆为证据提取和决策器，移除内部 LLM 调用 |
| `metadata_router.py` | 改为 R0/R1/R2 决策编排，不把 cosine 直接当置信度 |
| `metadata_schema.py` | 从 TaxonomySpec 派生枚举，严格校验 domain |
| `metadata_llm.py` | 保留为唯一统一 LLM 抽取入口 |
| `metadata_stage.py` | 接入任务化、有界并发和完整 fallback 契约 |
| `keyword_store_pg.py` | 增加枚举、审批、版本、审计和回滚 |
| `test_rule_freeze.py` | 从 hash 冻结扩展为规则版本回归门 |

## 12. 设计完成标准

- 任何分类结果都能说明来源、证据和版本。
- 规则修改不会绕过评估和审批直接影响线上。
- 低置信样本可以 abstain，不被强行分类。
- fallback 不调用 LLM，且返回完整契约。
- 影子、Embedding 和 LLM 不阻塞主索引。
- 高并发下具备队列、背压、幂等、缓存和熔断。
- 级联只有在逐层 precision、覆盖率、延迟和成本均达标后才开启。

## 13. 实施状态与上线运行口径（2026-09-20）

本设计已进入代码实施阶段。实现重点不是继续堆叠规则，而是把规则变成可审计的证据层和版本快照：规则变更先形成 draft，经审批发布；线上只读取 published snapshot；回滚只切换到已存在的历史版本，并通过数据库 advisory lock 防止多 worker 并发发布产生双主版本。

高并发路径采用“主请求短链路 + 异步影子链路”：主索引只执行统一抽取或已获准的级联决策，不等待影子评估；影子输入经过采样、脱敏/截断和 staging 后进入独立队列。LLM、Embedding、DB 和影子任务分别受信号量限制，缓存键包含 taxonomy、rules、model、prompt 版本，幂等键阻止重复任务。这样扩容 worker 时不会把外部模型和数据库无界打满。

上线默认值为 `METADATA_CASCADE_ENABLED=false`、`METADATA_CLASSIFIER_ENABLED=false`、`METADATA_CASCADE_ROLLOUT_PERCENT=0`。灰度由稳定 key 哈希到百分比桶，规则/模型路由指针可回滚；shadow queue 也有独立开关。发布校验工具对黄金集支持数、双人标注/仲裁证据、版本指纹、队列/负载/回滚证据执行 fail-closed，缺证据不允许自动放量。压测和回滚使用独立的 `metadata-load-v1`、`metadata-rollback-v1` JSON 证据，门禁校验 2× 峰值、队列不持续增长、429/重复写入为零、影子不抬高主路径 P95，以及旧指纹存在、幂等重放成功和 10 分钟内回滚。

企业节省 token 的控制点固定为：R0/R1 命中不调用 LLM；只有 R2 进入一次结构化抽取；R2 输入先截断/采样；相同版本的决策命中缓存；影子只采样并异步执行；fallback 禁止再调 LLM；低置信样本进入 abstain/人工复核而不是重复重试。成本指标必须与准确率、延迟和队列年龄一起看，不能用单纯降 token 换取错误率上升。

代码已覆盖 taxonomy/决策契约、版本化规则治理、确定性 fallback、有界并发、缓存/幂等、影子队列、灰度开关和 release gates。正式全量上线仍需真实黄金集、2 倍峰值压测、连续影子报告、回滚演练及安全合规证据；在证据完成前保持级联关闭。
