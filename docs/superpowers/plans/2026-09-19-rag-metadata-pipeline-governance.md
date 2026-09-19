# RAG 元数据管道治理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 RAG 文档元数据抽取改造成“证据规则 + 校准分类器 + 单次统一 LLM + 保守兜底”的可治理管道，并在现有 `rag_index` 队列上补齐有界并发、缓存、幂等、影子隔离和上线门禁。

**Architecture:** 以版本化 `TaxonomySpec` 作为 doc_type、business_domain、规则、Prompt 枚举和分类器标签的唯一事实源。`EvidenceExtractor` 只产生带版本的证据和候选，`DecisionRouter` 按 R0 高精度规则、R1 校准分类器、R2 单次 LLM、确定性 fallback 顺序决策；所有路径统一输出 `DecisionEnvelope`，再由 `MetadataStage` 转换为现有下游 metadata 字典。影子评估走独立任务和独立资源池，不进入主索引等待链路。

**Tech Stack:** Python 3、FastAPI、Pydantic v2、Celery、Redis、PostgreSQL、PyYAML、scikit-learn、joblib、NumPy、Prometheus、pytest。

**Spec:** `docs/superpowers/specs/2026-09-19-rag-metadata-pipeline-governance-design.md`

## Global Constraints

- 规则只负责证据提取、硬约束和安全兜底，不再承担复杂语义分类。
- `doc_type` 不新增；新增类型必须走 schema 演进流程，并同步更新 TaxonomySpec、评估集、Prompt 和兼容测试。
- 分类器必须输出可校准置信度并支持 `abstain`；原始 cosine 相似度不得直接作为最终置信度。
- LLM 只保留一个统一结构化抽取入口；规则仲裁、低置信复验和关键词 LLM 不得隐藏在 fallback 内部。
- fallback 禁止调用 LLM，必须返回完整 metadata 契约；冲突、低置信和高风险结果进入 `pending_review`。
- `METADATA_CASCADE_ENABLED` 默认保持 `false`，黄金集、影子流量和压测门禁未通过前不得打开。
- 上传索引继续使用现有 `rag_index` 队列；本计划不重写上传 API，不改变下游 chunk、registry 和向量库字段契约。
- Embedding、LLM、数据库和影子任务均使用有界并发；禁止无限制 `asyncio.gather` 和无上限线程池。
- 动态规则必须有枚举校验、权重边界、操作者、原因、审批、生效时间、不可变快照和回滚记录。
- 现有 `CascadeDecision`、`UnifiedMetadata` 和 `MetadataStage.build()` 调用方保持向后兼容；新契约通过适配器逐步迁移。
- 修改 Python 代码、YAML、Prompt 或规则数据后，`metadata_fingerprint` 必须变化；动态规则快照 hash 也必须纳入指纹。
- 每个实现任务先写强断言测试，再运行测试确认失败，完成最小实现后再次运行；局部 pytest 命令必须包含 `--no-cov`。
- 所有代码注释使用中文；不直接在业务代码中调用 `os.getenv`，配置集中在 `backend/config/`。

## Scope Rationale

本计划包含四个彼此有依赖的子系统：契约与 Taxonomy、证据/路由、并发运行时、评估与规则治理。它们不能独立上线：没有统一契约就无法切换路由，没有证据和分类器就无法验证门禁，没有资源隔离就无法进行影子压测，没有规则治理就无法保证切流后的可回滚性。因此保持一个计划，但每个任务都产出可独立测试和回滚的中间结果。

## File Map

### 新建文件

- `backend/rag/preprocessing/metadata_taxonomy.yaml`：doc_type、business_domain、别名、描述、阈值、静态规则、文件名/目录提示和 R0 allowlist 的版本化数据源。
- `backend/rag/preprocessing/taxonomy_spec.py`：加载、校验、规范化、版本和指纹计算。
- `backend/rag/preprocessing/metadata_evidence.py`：纯规则证据提取和冲突输出。
- `backend/rag/preprocessing/metadata_classifier.py`：在线加载、指纹校验、概率预测和 abstain。
- `backend/rag/preprocessing/metadata_decision.py`：R0/R1/R2/fallback 统一决策器。
- `backend/rag/preprocessing/metadata_runtime.py`：按事件循环隔离的有界并发、缓存键和运行时资源封装。
- `backend/rag/preprocessing/metadata_shadow.py`：影子输入暂存、非阻塞投递和结果记录。
- `backend/tasks/metadata_shadow_tasks.py`：独立影子 Celery 任务。
- `backend/sql/migrations/025_metadata_rule_governance.sql`：动态规则版本、审批、快照和审计表。
- `backend/sql/migrations/026_metadata_shadow_jobs.sql`：影子任务状态和结果表。
- `backend/tests/rag/test_taxonomy_spec.py`：TaxonomySpec 和指纹测试。
- `backend/tests/rag/test_metadata_evidence.py`：证据层和冲突测试。
- `backend/tests/rag/test_metadata_classifier.py`：在线分类器契约和 abstain 测试。
- `backend/tests/rag/test_metadata_decision.py`：R0/R1/R2/fallback 路由测试。
- `backend/tests/rag/test_metadata_runtime.py`：并发、缓存和幂等测试。
- `backend/tests/rag/test_metadata_shadow.py`：影子异步投递和故障隔离测试。
- `backend/tests/rag/test_metadata_rule_governance.py`：规则版本和审批测试。
- `backend/tests/eval/test_metadata_release_gates.py`：发布门禁计算测试。
- `backend/eval/metadata_baseline/validate_release.py`：黄金集、分类器、影子和压测报告门禁检查器。

### 修改文件

- `backend/rag/preprocessing/metadata_schema.py`：由 TaxonomySpec 派生枚举；严格校验 domain；新增 `DecisionEnvelope`、证据、候选和版本字段。
- `backend/rag/preprocessing/domain_data.py`：保留兼容导出，但不再保存第二份 doc_type 规则；静态规则从 TaxonomySpec 派生。
- `backend/rag/preprocessing/metadata_llm.py`：使用 TaxonomySpec 派生 Prompt 枚举、统一 Schema 校验、LLM 并发门和抽取缓存。
- `backend/prompts/defaults/rag_preprocessing_metadata_extract.yaml`：补充 canonical domain 枚举和别名约束。
- `backend/prompts/registry.py`：只在变量清单需要扩展时同步声明 `domains`；不新增第二份枚举。
- `backend/rag/preprocessing/metadata.py`：移除隐式 LLM 仲裁；规则分类只返回证据/候选兼容结果。
- `backend/rag/preprocessing/keyword.py`：增加显式 `allow_llm` 参数；fallback 传 `False`。
- `backend/rag/preprocessing/metadata_router.py`：改为新决策器的兼容门面；不再把 cosine 直接写成置信度。
- `backend/rag/indexing/stages/metadata_stage.py`：统一调用 DecisionRouter，补齐 fallback 契约，并将影子改为非阻塞投递。
- `backend/config/rag.py`：新增模型路径、阈值、并发、缓存、影子和背压配置；扩展 metadata 指纹输入文件。
- `backend/config/tasks.py`：新增影子队列和影子任务重试/TTL配置。
- `backend/tasks/celery_app.py`：注册 `rag_metadata_shadow` 队列并保持 `rag_index` 与 `agent` 隔离。
- `backend/rag/preprocessing/keyword_store_pg.py`：读写活动规则快照，拒绝未登记 doc_type 和越界权重。
- `backend/rag/preprocessing/keyword_store.py`：补充快照、发布、回滚接口并保持旧读取接口。
- `backend/app/api/routes/keyword_routes.py`：写操作改为草稿/发布/回滚流程，并加入管理员鉴权和审计字段。
- `frontend-admin/src/api/keyword.ts`：增加规则草稿、发布、回滚接口类型。
- `frontend-admin/src/app/knowledge/keywords/page.tsx`：显示规则版本/审核状态，提交后不直接假设线上已生效。
- `backend/eval/metadata_baseline/lr_features.py`：从 TaxonomySpec 派生特征顺序和规则，不读取重复常量。
- `backend/eval/metadata_baseline/train_lr.py`：按文档来源/文档族拆分，增加概率校准、逐类阈值和模型卡指纹。
- `backend/eval/metadata_baseline/predict.py`：真实传入 embedding，输出 route source、abstain、版本和延迟。
- `backend/eval/metadata_baseline/evaluate.py`：增加 coverage、abstain、ECE、风险字段召回和路径级指标；现网 LLM 一致率只作诊断。
- `backend/observability/metrics.py`：增加路由延迟、置信度、队列等待、资源并发、影子任务和规则版本指标。
- `backend/tests/rag/test_metadata_schema.py`、`test_metadata_llm.py`、`test_metadata_router.py`、`test_metadata_stage_cascade.py`、`test_rule_freeze.py`：迁移到新契约并保留兼容断言。
- `backend/tests/eval/test_metadata_baseline_eval.py`：覆盖新指标和预测文件格式。
- `docs/2026-09-19-RAG元数据管道统一抽取与级联路由上线规划.md`：实施完成后回填实际结果、门禁和当前开关状态，不提前宣称切流完成。

---

### Task 1: 建立 TaxonomySpec 和统一决策契约

**Files:**
- Create: `backend/rag/preprocessing/metadata_taxonomy.yaml`
- Create: `backend/rag/preprocessing/taxonomy_spec.py`
- Modify: `backend/rag/preprocessing/metadata_schema.py`
- Modify: `backend/rag/preprocessing/metadata_llm.py`
- Modify: `backend/prompts/defaults/rag_preprocessing_metadata_extract.yaml`
- Modify: `backend/prompts/registry.py`
- Modify: `backend/config/rag.py`
- Create: `backend/tests/rag/test_taxonomy_spec.py`
- Modify: `backend/tests/rag/test_metadata_schema.py`
- Modify: `backend/tests/rag/test_metadata_llm.py`

**Interfaces:**
- Produces `get_taxonomy() -> MetadataTaxonomy`.
- Produces `normalize_doc_type(value: object) -> str` and `normalize_domain(value: object) -> str`.
- Produces `taxonomy_fingerprint() -> str` and `metadata_rule_version() -> str`.
- Produces `DecisionEnvelope.model_validate(payload) -> DecisionEnvelope`.
- Existing `UnifiedMetadata.model_validate(payload)` and `to_extract_dict()` remain callable.

- [ ] **Step 1: Write failing TaxonomySpec tests.**

```python
def test_taxonomy_has_14_existing_doc_types_and_11_domains():
    taxonomy = get_taxonomy()
    assert len(taxonomy.doc_types) == 14
    assert set(taxonomy.doc_types) == set(DOC_TYPES)
    assert set(taxonomy.domains) == set(DOMAINS)


def test_domain_legacy_aliases_normalize_to_canonical_values():
    assert normalize_domain("finance") == "financial"
    assert normalize_domain("marketing") == "advertising"
    assert normalize_domain("after_sale") == "customer"
    assert normalize_domain("unknown-domain") == "general"


def test_taxonomy_fingerprint_changes_when_catalog_changes(tmp_path):
    original = taxonomy_fingerprint()
    altered = tmp_path / "metadata_taxonomy.yaml"
    altered.write_text(get_taxonomy().raw_text + "\n# changed\n", encoding="utf-8")
    assert fingerprint_for_path(altered) != original
```

- [ ] **Step 2: Run the focused tests and verify they fail for the missing loader/contract.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_taxonomy_spec.py backend/tests/rag/test_metadata_schema.py -q --no-cov`

Expected: FAIL because the new TaxonomySpec module, strict domain normalization and `DecisionEnvelope` do not exist yet; do not weaken assertions to make the pre-implementation run pass.

- [ ] **Step 3: Move the current 14 doc_type descriptions, 11 canonical domains, aliases, current regex rules, filename hints and folder hints into `metadata_taxonomy.yaml`.**

The YAML must contain `version: v1`, one entry per existing `DOC_TYPES`, one entry per existing `DOMAINS`, and stable `rule_id` values. The canonical domain list must remain `product/order/inventory/logistics/advertising/customer/supplier/analytics/data/financial/general`. The compatibility aliases are data aliases, not new domains.

- [ ] **Step 4: Implement the loader with fail-fast validation and stable hashing.**

Expose the following shape:

```python
@dataclass(frozen=True)
class MetadataTaxonomy:
    version: str
    doc_types: Sequence[str]
    domains: Sequence[str]
    doc_type_descriptions: Mapping[str, str]
    doc_type_rules: Mapping[str, Sequence[RuleSpec]]
    filename_hints: Mapping[str, str]
    folder_hints: Mapping[str, str]
    domain_rules: Mapping[str, Mapping[str, int]]
    aliases: Mapping[str, Mapping[str, str]]
    r0_allowlist: frozenset[str]
    raw_text: str

    def normalize_doc_type(self, value: object) -> str:
        raise NotImplementedError

    def normalize_domain(self, value: object) -> str:
        raise NotImplementedError

    def legacy_doc_type_rules(self) -> dict[str, list[tuple[str, int]]]:
        raise NotImplementedError


def get_taxonomy() -> MetadataTaxonomy:
    raise NotImplementedError


def fingerprint_for_path(path: Path) -> str:
    raise NotImplementedError


def taxonomy_fingerprint() -> str:
    raise NotImplementedError
```

Loader requirements: duplicate labels, unknown rule targets, invalid regex, non-positive weights, unknown R0 rule IDs and missing `general` must raise a concrete `TaxonomyConfigError` during import/startup. The loader must use `yaml.safe_load`, canonical JSON ordering and SHA-256 for the fingerprint.

- [ ] **Step 5: Add `DecisionEnvelope` and strict domain validation without breaking old outputs.**

Add Pydantic models for `DecisionCandidate`, `EvidenceItem` and `DecisionEnvelope`. `DecisionEnvelope` must contain `decision`, `doc_type`, `business_domain`, `confidence`, `candidates`, `source`, `evidence`, `conflicts`, `taxonomy_version`, `rules_version`, `model_version`, `prompt_version`, `fallback_reason`, `latency_ms` and `llm_call_count`. `decision` is one of `accepted/abstain/fallback/review`; `source` is one of `r0/r1/llm/fallback/human`.

`UnifiedMetadata._norm_domain` must call `normalize_domain`; unknown values become `general` only through the documented compatibility normalization path, while `DecisionEnvelope` rejects a non-canonical domain after normalization. Update the existing `GOOD` test fixture from `finance` to the canonical output and add an explicit alias test.

- [ ] **Step 6: Make the metadata Prompt derive both doc_type and domain enums from the same source.**

Add a `domains` template variable, render canonical domains in `metadata_llm.py`, and replace the current free-form example text (`after_sale、order、customer、logistics、product、marketing、finance`) with a strict “必须从枚举选择；无法判断使用 general” instruction. Keep `prompt_version` in the result and include the Prompt file in the metadata fingerprint.

- [ ] **Step 7: Expand the fingerprint inputs and run compatibility tests.**

`compute_metadata_fingerprint()` must include `metadata_taxonomy.yaml`, `taxonomy_spec.py`, `metadata_schema.py`, `metadata_llm.py`, the metadata Prompt, `metadata_router.py`, `metadata.py`, `keyword.py` and the active model-card path when it exists. Preserve the existing 12-character SHA-256 output format.

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_taxonomy_spec.py backend/tests/rag/test_metadata_schema.py backend/tests/rag/test_metadata_llm.py -q --no-cov`

Expected: PASS, including old `UnifiedMetadata.to_extract_dict()` keys plus `risk`.

- [ ] **Step 8: Commit the contract-only change.**

```bash
git add backend/rag/preprocessing/metadata_taxonomy.yaml backend/rag/preprocessing/taxonomy_spec.py backend/rag/preprocessing/metadata_schema.py backend/rag/preprocessing/metadata_llm.py backend/prompts/defaults/rag_preprocessing_metadata_extract.yaml backend/prompts/registry.py backend/config/rag.py backend/tests/rag/test_taxonomy_spec.py backend/tests/rag/test_metadata_schema.py backend/tests/rag/test_metadata_llm.py
git commit -m "feat: 建立元数据 taxonomy 与统一决策契约"
```

---

### Task 2: 将规则改造成版本化证据层

**Files:**
- Create: `backend/rag/preprocessing/metadata_evidence.py`
- Modify: `backend/rag/preprocessing/domain_data.py`
- Modify: `backend/rag/preprocessing/metadata.py`
- Modify: `backend/rag/preprocessing/metadata_router.py`
- Modify: `backend/eval/metadata_baseline/lr_features.py`
- Create: `backend/tests/rag/test_metadata_evidence.py`
- Modify: `backend/tests/rag/test_metadata_router.py`
- Modify: `backend/tests/rag/test_rule_freeze.py`
- Modify: `backend/tests/rag/test_classify.py`

**Interfaces:**
- Produces `EvidenceSignal`、`EvidenceBundle`。
- Produces `extract_evidence(full_text: str, filename: str = "", file_path: str = "") -> EvidenceBundle`。
- Produces `EvidenceBundle.to_trace_dict() -> dict`。
- `metadata_router._l0_strong_prior()` remains callable as a compatibility wrapper but delegates to the evidence implementation。

- [ ] **Step 1: Write failing evidence tests for strong, weak and conflicting signals.**

```python
def test_filename_and_folder_conflict_never_becomes_r0_decision():
    evidence = extract_evidence("正文", "合同制度.docx", "docs/finance/合同制度.docx")
    assert "legal" in evidence.candidates
    assert "policy" in evidence.candidates or "financial" in evidence.candidates
    assert evidence.conflicts
    assert evidence.r0_eligible is False


def test_generic_word_is_weak_evidence_only():
    evidence = extract_evidence("本制度适用于订单处理", "unknown.docx", "")
    assert all(signal.strength != "strong" for signal in evidence.signals)
    assert evidence.r0_eligible is False


def test_every_signal_has_rule_id_and_version():
    evidence = extract_evidence("合同由甲方与乙方签订", "unknown.docx", "")
    assert evidence.signals
    assert all(s.rule_id and s.rules_version for s in evidence.signals)
```

- [ ] **Step 2: Run the evidence tests and verify they fail because the evidence types and extractor do not exist.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_metadata_evidence.py -q --no-cov`

Expected: FAIL with import errors for `metadata_evidence` or missing evidence fields.

- [ ] **Step 3: Implement the pure evidence data types and text normalization.**

Use this interface:

```python
@dataclass(frozen=True)
class EvidenceSignal:
    rule_id: str
    value: str
    source: Literal["filename", "path", "body", "structure", "dynamic"]
    strength: Literal["strong", "weak"]
    target: str | None
    score: float
    rules_version: str


@dataclass(frozen=True)
class EvidenceBundle:
    signals: Sequence[EvidenceSignal]
    candidates: Sequence[str]
    conflicts: Sequence[str]
    features: dict[str, float]
    r0_eligible: bool
    rules_version: str

    def to_trace_dict(self) -> dict:
        raise NotImplementedError


def extract_evidence(full_text: str, filename: str = "",
                     file_path: str = "") -> EvidenceBundle:
    raise NotImplementedError
```

The extractor may compile and apply catalog regexes, count structures, and collect filename/path hits. It must never select a final doc_type, call LLM, call Embedding or read uncached mutable global rule state. Dynamic rules must come from an immutable active snapshot supplied by the rule store.

- [ ] **Step 4: Mark only approved high-precision rules as R0 eligible.**

Every R0 signal must have a catalog `rule_id` in `r0_allowlist`, target exactly one canonical doc_type, and pass the no-conflict check. A generic body hit such as `制度` or `合同` must remain weak unless the catalog explicitly marks a validated compound pattern as strong. Add a helper:

```python
def r0_candidate(evidence: EvidenceBundle) -> str | None:
    """无冲突高精度证据返回唯一类型，否则返回 None。"""
```

- [ ] **Step 5: Replace duplicate static dictionaries with compatibility exports.**

`domain_data.py` must keep `DOC_TYPE_RULES`, `FILENAME_TYPE_HINTS`, `FOLDER_TYPE_HINTS` and `DOMAIN_RULES` names for existing callers, but derive them from `get_taxonomy().legacy_doc_type_rules()` and related accessors. `metadata_router.TAXONOMY_DESCRIPTIONS` must derive from TaxonomySpec, and `lr_features.py` must derive feature order from `get_taxonomy().doc_types`. No new regex may be added to Python files.

- [ ] **Step 6: Remove hidden LLM behavior from the rule classifier.**

`metadata.py::classify_with_confidence` must return deterministic scores/details only. Remove the internal arbitration call from the default path. Keep an explicit `allow_llm_arbitration` parameter only if a legacy diagnostic caller still needs it; metadata production fallback must pass `False`. Add a test that monkeypatches the LLM entry point to raise and verifies classification still returns a deterministic result.

- [ ] **Step 7: Update freeze tests to cover catalog content and fingerprint.**

The freeze test must hash the YAML catalog and active snapshot schema, not only `domain_data.py`. It must assert dynamic rules are excluded from the immutable static catalog but included through a separate runtime snapshot hash. Preserve a readable failure message that names the changed artifact and instructs the operator to run the golden-set regression.

- [ ] **Step 8: Run the rule and router regression suite.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_metadata_evidence.py backend/tests/rag/test_metadata_router.py backend/tests/rag/test_rule_freeze.py backend/tests/rag/test_classify.py -q --no-cov`

Expected: PASS; existing L0/L1/L2 tests may still exercise the compatibility facade, but no test may require a hidden LLM call from the rule layer.

- [ ] **Step 9: Commit the evidence-layer change.**

```bash
git add backend/rag/preprocessing/metadata_evidence.py backend/rag/preprocessing/domain_data.py backend/rag/preprocessing/metadata.py backend/rag/preprocessing/metadata_router.py backend/eval/metadata_baseline/lr_features.py backend/tests/rag/test_metadata_evidence.py backend/tests/rag/test_metadata_router.py backend/tests/rag/test_rule_freeze.py backend/tests/rag/test_classify.py
git commit -m "refactor: 将元数据规则收敛为版本化证据层"
```

---

### Task 3: 上线校准分类器 R1，并禁止模型失配继续路由

**Files:**
- Create: `backend/rag/preprocessing/metadata_classifier.py`
- Modify: `backend/eval/metadata_baseline/lr_features.py`
- Modify: `backend/eval/metadata_baseline/train_lr.py`
- Modify: `backend/config/rag.py`
- Create: `backend/tests/rag/test_metadata_classifier.py`
- Create: `backend/tests/eval/test_metadata_classifier_training.py`

**Interfaces:**
- Produces `MetadataClassifier.load(path: Path) -> MetadataClassifier | None`。
- Produces `MetadataClassifier(clf: Any, card: Mapping[str, Any])` for unit-test construction and artifact loading。
- Produces `MetadataClassifier.predict(text: str, filename: str, file_path: str, embedding_sims: Mapping[str, float] | None) -> ClassifierPrediction`。
- `ClassifierPrediction` contains `label`, `confidence`, `candidates`, `accepted`, `abstain_reason`, `model_version`, `feature_version`。
- Existing training command remains `python -m backend.eval.metadata_baseline.train_lr --golden backend/eval/metadata_baseline/golden_v1.jsonl --out backend/data/models/metadata_lr`，新增 `--calibrate` and `--embedding` behavior stays explicit.

- [ ] **Step 1: Write failing online classifier tests.**

```python
def test_model_fingerprint_mismatch_returns_none(tmp_path, monkeypatch):
    artifact = tmp_path / "lr_model.joblib"
    joblib.dump({"clf": FakeClassifier(), "card": {"taxonomy_fingerprint": "old"}}, artifact)
    monkeypatch.setattr("backend.rag.preprocessing.metadata_classifier.taxonomy_fingerprint", lambda: "new")
    assert MetadataClassifier.load(artifact) is None


def test_probability_below_per_class_threshold_abstains(fake_loaded_classifier):
    prediction = fake_loaded_classifier.predict("正文", "unknown.docx", "", None)
    assert prediction.accepted is False
    assert prediction.abstain_reason == "below_class_threshold"


def test_top1_margin_is_required_for_acceptance(fake_loaded_classifier):
    prediction = fake_loaded_classifier.predict("冲突正文", "unknown.docx", "", None)
    assert prediction.accepted is False
    assert prediction.abstain_reason == "insufficient_margin"
```

- [ ] **Step 2: Run the classifier tests and verify they fail before the online loader exists.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_metadata_classifier.py backend/tests/eval/test_metadata_classifier_training.py -q --no-cov`

Expected: FAIL because `metadata_classifier.py` and calibrated model-card fields do not exist.

- [ ] **Step 3: Extend the training script to produce calibrated probabilities and class thresholds.**

Use `CalibratedClassifierCV` around the current LogisticRegression, keep `feature_names` and `embedding_features_on` in the model card, and add:

```json
{
  "model_version": "metadata-lr-golden123-taxonomy123",
  "taxonomy_fingerprint": "sha256:taxonomy-v1",
  "rules_version": "rules-2026-09-19-v1",
  "feature_version": "metadata-features-v2",
  "calibration": "sigmoid",
  "accept_thresholds": {"legal": 0.98, "policy": 0.98},
  "min_margin": 0.05,
  "per_label_precision": {"legal": 0.99},
  "coverage": 0.80
}
```

Split by `source` or `doc_family` before calibration; if the golden rows do not have a source/family field, fail the formal training command with a clear message rather than mixing near-duplicate documents across train and validation. `--dry-run` may use the seed set but must mark the artifact non-promotable.

- [ ] **Step 4: Implement strict model loading and prediction.**

`MetadataClassifier.load()` must verify artifact existence, joblib structure, taxonomy fingerprint, rules fingerprint, feature names and `embedding_features_on`. Any mismatch returns `None` and increments a model-mismatch metric; it must not raise into the indexing path. Prediction must sort top-k probabilities, apply the class threshold and top1-top2 margin, and return `accepted=False` with a stable reason on abstain.

- [ ] **Step 5: Add configuration without enabling online acceptance.**

Add `METADATA_CLASSIFIER_ENABLED=false`, `METADATA_CLASSIFIER_MODEL_PATH`, `METADATA_CLASSIFIER_MIN_MARGIN` and `METADATA_CLASSIFIER_LOAD_TIMEOUT` to `backend/config/rag.py`. The default configuration must make the router skip R1 and continue to R2 until the release gate is explicitly enabled.

- [ ] **Step 6: Run model and feature tests.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_metadata_classifier.py backend/tests/eval/test_metadata_classifier_training.py backend/tests/eval/test_metadata_baseline_eval.py -q --no-cov`

Expected: PASS; a missing or stale model must produce an abstain/skip result and never become an accepted route.

- [ ] **Step 7: Commit the classifier change.**

```bash
git add backend/rag/preprocessing/metadata_classifier.py backend/eval/metadata_baseline/lr_features.py backend/eval/metadata_baseline/train_lr.py backend/config/rag.py backend/tests/rag/test_metadata_classifier.py backend/tests/eval/test_metadata_classifier_training.py backend/tests/eval/test_metadata_baseline_eval.py
git commit -m "feat: 增加可校准的元数据分类器路由"
```

---

### Task 4: 实现 R0/R1/R2/fallback 决策器并接入 MetadataStage

**Files:**
- Create: `backend/rag/preprocessing/metadata_decision.py`
- Modify: `backend/rag/preprocessing/metadata_router.py`
- Modify: `backend/rag/preprocessing/metadata_llm.py`
- Modify: `backend/rag/preprocessing/metadata.py`
- Modify: `backend/rag/preprocessing/keyword.py`
- Modify: `backend/rag/indexing/stages/metadata_stage.py`
- Create: `backend/tests/rag/test_metadata_decision.py`
- Modify: `backend/tests/rag/test_metadata_router.py`
- Modify: `backend/tests/rag/test_metadata_stage_cascade.py`
- Modify: `backend/tests/rag/test_metadata_llm.py`

**Interfaces:**
- Produces `async decide_metadata(full_text: str, filename: str, file_path: str = "", embedding=None, parent_span_id: str = "") -> DecisionEnvelope`。
- `metadata_router.cascade_route()` remains an adapter returning `CascadeDecision` for old callers。
- Produces `build_deterministic_fallback(full_text: str, filename: str, file_path: str, evidence: EvidenceBundle, reason: str) -> DecisionEnvelope`。
- Produces `MetadataStage.finalize_decision(full_text, base_meta, envelope, parent_span_id, chunks_text) -> dict` with the existing downstream key set plus `decision_envelope` and `fallback_reason`。

- [ ] **Step 1: Write failing route tests for all decision branches.**

```python
@pytest.mark.asyncio
async def test_r0_accepts_only_unique_strong_signal(monkeypatch):
    result = await decide_metadata("固定编号合同", "approved-contract-id.docx", embedding=None)
    assert result.source == "r0"
    assert result.decision == "accepted"
    assert result.llm_call_count == 0


async def fake_llm_result(*args, **kwargs):
    return {"doc_type": "legal", "confidence": 0.9, "business_domain": "general",
            "summary": "s", "keywords": [], "entities": {}, "time_refs": [],
            "risk": {"level": "none", "signals": []}}


class _FakeEmbedding:
    model_name = "metadata-test-embedding"

    def embed_documents(self, texts):
        return [[1.0 if i == j else 0.0 for j in range(len(texts))]
                for i in range(len(texts))]

    def embed_query(self, text):
        return [1.0] + [0.0] * 13


fake_embedding = _FakeEmbedding()


async def low_confidence_prediction(*args, **kwargs):
    return ClassifierPrediction(label="legal", confidence=0.55,
                                candidates=[{"label": "legal", "score": 0.55},
                                            {"label": "policy", "score": 0.54}],
                                accepted=False, abstain_reason="below_class_threshold",
                                model_version="test-model", feature_version="test-features")


@pytest.mark.asyncio
async def test_r1_abstain_falls_to_r2(monkeypatch):
    monkeypatch.setattr("backend.rag.preprocessing.metadata_decision._classifier_prediction", low_confidence_prediction)
    monkeypatch.setattr("backend.rag.preprocessing.metadata_decision.extract_metadata_llm_async", fake_llm_result)
    result = await decide_metadata("模糊正文", "unknown.docx", embedding=fake_embedding)
    assert result.source == "llm"
    assert result.decision == "accepted"


@pytest.mark.asyncio
async def test_llm_failure_uses_complete_deterministic_fallback(monkeypatch):
    monkeypatch.setattr("backend.rag.preprocessing.metadata_decision.extract_metadata_llm_async", lambda *a, **k: None)
    result = await decide_metadata("无法判断的正文", "unknown.docx", embedding=None)
    assert result.source == "fallback"
    assert result.decision in {"fallback", "review"}
    assert result.doc_type == "general"
    assert result.fallback_reason == "llm_unavailable"
```

- [ ] **Step 2: Run the decision tests and verify they fail before the decision module exists.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_metadata_decision.py -q --no-cov`

Expected: FAIL on missing `decide_metadata` and `DecisionEnvelope.llm_call_count` route metadata.

- [ ] **Step 3: Implement the decision order and route metadata.**

The decision algorithm is fixed:

```python
evidence = extract_evidence(full_text, filename, file_path)
if r0_candidate(evidence) is not None:
    return accepted(source="r0", evidence=evidence)

prediction = await _classifier_prediction(full_text, filename, file_path, embedding, evidence)
if prediction is not None and prediction.accepted:
    return accepted(source="r1", candidates=prediction.candidates, evidence=evidence)

llm_result = await extract_metadata_llm_async(full_text, filename, parent_span_id=parent_span_id)
if llm_result is not None:
    return accepted(source="llm", metadata=llm_result, evidence=evidence)

return build_deterministic_fallback(full_text, filename, file_path, evidence, reason="llm_unavailable")
```

R1 must be skipped when the model is missing, stale, disabled or embedding features are unavailable. R2 must be called at most once per document decision. The router must record the exact reason for each miss/abstain and never treat a raw similarity score as probability.

- [ ] **Step 4: Make fallback deterministic and complete.**

Fallback must use `general` for insufficient evidence, preserve a unique validated R0 type if one exists, set `confidence` to a configured low value, set `decision` to `fallback` or `review`, include evidence/conflicts/versions, and return summary, keywords, entities, time_refs and risk with valid empty/default values. It must not call `extract_metadata_llm_async`, `invoke_metadata_llm`, `build_llm_summary`, `extract_doc_keywords_typed` with LLM enabled, or the old low-confidence reverify block.

- [ ] **Step 5: Add explicit `allow_llm` to keyword enrichment and remove hidden arbitration.**

Change:

```python
def extract_doc_keywords_typed(
    text: str,
    doc_type: str = "general",
    top_k: int = 10,
    confidence: float = 0.0,
    complexity: dict | None = None,
    *,
    allow_llm: bool = True,
) -> KeywordResult:
    raise NotImplementedError
```

When `allow_llm=False`, return rule keywords only, set `llm_keywords=[]`, `llm_tokens={}`, `llm_strategy="deterministic"` and preserve the `KeywordResult` shape. The fallback finalizer must pass `allow_llm=False`; the normal unified LLM path may keep explicit enrichment behavior outside the decision route.

- [ ] **Step 6: Switch `MetadataStage.build()` to the new decision contract.**

The stage must validate the returned `DecisionEnvelope`, call one finalizer for all sources, and keep the existing keys checked by `test_metadata_stage_cascade.py`. The existing `finalize_unified()` and `finalize_cascade()` methods may delegate to `finalize_decision()` for compatibility, but the new path must set `llm_strategy` and `llm_decision` from the envelope rather than reconstructing them from scattered branches. Replace the exception return `{"doc_type": "general"}` with a complete deterministic fallback result.

- [ ] **Step 7: Add no-hidden-LLM and contract tests.**

Monkeypatch every metadata LLM entry point to raise an assertion error, run an LLM-failure path, and assert the returned dictionary contains `doc_type`, `confidence`, `business_domain`, `summary`, `doc_keywords`, `keywords_rule`, `keywords_llm`, `entities`, `time_refs`, `risk`, `llm_used`, `llm_strategy`, `llm_decision`, `metadata_fingerprint`, `sections`, `quality_score`, `minhash_sig`, `near_dup_id`, `department` and `questions_by_chunk`. Add a test that a malformed LLM domain is normalized/rejected by the same Schema as every other source.

- [ ] **Step 8: Run the route and stage suite.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_metadata_decision.py backend/tests/rag/test_metadata_router.py backend/tests/rag/test_metadata_llm.py backend/tests/rag/test_metadata_stage_cascade.py backend/tests/rag/test_simulated_questions_pipeline.py -q --no-cov`

Expected: PASS; cascade remains disabled by default and legacy callers still receive `CascadeDecision`.

- [ ] **Step 9: Commit the decision and fallback change.**

```bash
git add backend/rag/preprocessing/metadata_decision.py backend/rag/preprocessing/metadata_router.py backend/rag/preprocessing/metadata_llm.py backend/rag/preprocessing/metadata.py backend/rag/preprocessing/keyword.py backend/rag/indexing/stages/metadata_stage.py backend/tests/rag/test_metadata_decision.py backend/tests/rag/test_metadata_router.py backend/tests/rag/test_metadata_llm.py backend/tests/rag/test_metadata_stage_cascade.py backend/tests/rag/test_simulated_questions_pipeline.py
git commit -m "refactor: 统一元数据决策与确定性兜底"
```

---

### Task 5: 增加有界并发、缓存、背压和幂等运行时

**Files:**
- Create: `backend/rag/preprocessing/metadata_runtime.py`
- Modify: `backend/rag/preprocessing/metadata_llm.py`
- Modify: `backend/rag/preprocessing/metadata_router.py`
- Modify: `backend/rag/indexing/stages/metadata_stage.py`
- Modify: `backend/config/rag.py`
- Modify: `backend/config/tasks.py`
- Modify: `backend/observability/metrics.py`
- Create: `backend/tests/rag/test_metadata_runtime.py`
- Modify: `backend/tests/rag/test_metadata_llm.py`
- Modify: `backend/tests/rag/test_metadata_router.py`

**Interfaces:**
- Produces `get_metadata_limiters() -> MetadataLimiters`，按当前 asyncio event loop 隔离实例。
- Produces `metadata_cache_key(text, filename, file_path, taxonomy_version, model_version, prompt_version) -> str`。
- Produces `with_metadata_limit(resource: Literal["embedding", "llm", "db", "shadow"], timeout: float)`。
- Produces `run_limited(resource: str, operation: Callable[[], Awaitable[T]], timeout: float | None = None) -> T`。
- Produces `idempotency_key(file_hash, taxonomy_version, rules_version, model_version) -> str`。

- [ ] **Step 1: Write failing limiter and cache tests.**

```python
@pytest.mark.asyncio
async def test_llm_limiter_never_exceeds_configured_width(monkeypatch):
    monkeypatch.setattr(config, "METADATA_LLM_CONCURRENCY", 2)
    active = 0
    peak = 0

    async def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    await asyncio.gather(*(run_limited("llm", work) for _ in range(8)))
    assert peak <= 2


def test_metadata_cache_key_changes_with_model_and_rule_versions():
    key1 = metadata_cache_key("text", "a.docx", "", "tax-v1", "model-a", 2)
    key2 = metadata_cache_key("text", "a.docx", "", "tax-v1", "model-b", 2)
    assert key1 != key2
```

- [ ] **Step 2: Run the runtime tests and verify they fail before the limiter/cache module exists.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_metadata_runtime.py -q --no-cov`

Expected: FAIL because the limiters, cache key and config fields do not exist.

- [ ] **Step 3: Implement loop-safe resource limiters.**

Use a `WeakKeyDictionary` keyed by the running event loop. Each loop gets independent semaphores for `embedding`, `llm`, `db` and `shadow`, each configured from `backend/config/rag.py`. A timeout must raise a typed `MetadataResourceTimeout` to the caller; the decision router converts it to a route miss/fallback and emits a metric. Never share an asyncio semaphore created by one loop with another loop.

- [ ] **Step 4: Add cache and idempotency keys using existing cache infrastructure.**

Use `backend.infra.cache.get_cache("rag_metadata", ttl=METADATA_CACHE_TTL_SECONDS)` for L1/L2 behavior. Cache values must be Schema-validated `DecisionEnvelope` dictionaries, not arbitrary objects. The key must include normalized text hash, filename/path hash, Taxonomy fingerprint, rules snapshot hash, model version and Prompt version. Cache failures are soft misses. Store writes must use the same key as reads and never write failed/abstain results unless explicitly configured for diagnostic caching.

- [ ] **Step 5: Apply resource limits at the real external boundaries.**

Wrap `embedding.embed_documents/embed_query`, `async_safe_call_with_timeout(invoke_metadata_llm, LLM_REQUEST_TIMEOUT, None, timeout_message, prompt)`, metadata cache/DB snapshot reads and shadow dispatch with the corresponding limiter. Keep existing `RAG_MAX_CONCURRENT_INDEX` as the outer memory gate; do not replace it with a second unconstrained semaphore. Add config defaults:

```text
METADATA_EMBED_CONCURRENCY=4
METADATA_LLM_CONCURRENCY=8
METADATA_DB_CONCURRENCY=16
METADATA_SHADOW_CONCURRENCY=2
METADATA_RESOURCE_WAIT_TIMEOUT=5
METADATA_CACHE_TTL_SECONDS=604800
METADATA_IDEMPOTENCY_TTL_SECONDS=86400
```

- [ ] **Step 6: Enforce retry and backpressure semantics.**

429, timeout and connection errors must use the existing Celery retry policy with exponential backoff and jitter; no inner loop may retry without a fixed maximum. Resource wait timeout must record `metadata_resource_wait_timeout_total` and use the next safe route. A cache or shadow queue full condition must never fail the primary index task. The final metadata write must be guarded by `(file_hash, taxonomy_version, rules_version, model_version)` idempotency key so Celery redelivery cannot duplicate the effective result.

- [ ] **Step 7: Add runtime metrics and tests.**

Add counters/histograms for `metadata_route_latency_seconds`, `metadata_queue_wait_seconds`, `metadata_resource_wait_timeout_total`, `metadata_cache_total{result}`, `metadata_llm_calls_total{result}`, `metadata_shadow_dispatch_total{result}` and gauges for active/queued resource slots. Tests must verify concurrency width, cache hit avoids LLM, stale versions miss cache, timeout falls through, and duplicate task writes are idempotent.

- [ ] **Step 8: Run the runtime suite.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_metadata_runtime.py backend/tests/rag/test_metadata_llm.py backend/tests/rag/test_metadata_router.py backend/tests/test_rag_upload_concurrency.py backend/tests/test_upload_resilience.py -q --no-cov`

Expected: PASS; existing upload concurrency behavior remains intact and metadata-specific limits are observable.

- [ ] **Step 9: Commit the runtime-control change.**

```bash
git add backend/rag/preprocessing/metadata_runtime.py backend/rag/preprocessing/metadata_llm.py backend/rag/preprocessing/metadata_router.py backend/rag/indexing/stages/metadata_stage.py backend/config/rag.py backend/config/tasks.py backend/observability/metrics.py backend/tests/rag/test_metadata_runtime.py backend/tests/rag/test_metadata_llm.py backend/tests/rag/test_metadata_router.py backend/tests/test_rag_upload_concurrency.py backend/tests/test_upload_resilience.py
git commit -m "feat: 增加元数据管道有界并发与缓存控制"
```

---

### Task 6: 将影子评估移出主路径并隔离队列

**Files:**
- Create: `backend/rag/preprocessing/metadata_shadow.py`
- Create: `backend/tasks/metadata_shadow_tasks.py`
- Create: `backend/sql/migrations/026_metadata_shadow_jobs.sql`
- Modify: `backend/tasks/celery_app.py`
- Modify: `backend/config/tasks.py`
- Modify: `backend/rag/indexing/stages/metadata_stage.py`
- Modify: `backend/rag/preprocessing/metadata_router.py`
- Modify: `backend/observability/metrics.py`
- Create: `backend/tests/rag/test_metadata_shadow.py`
- Modify: `backend/tests/rag/test_metadata_stage_cascade.py`

**Interfaces:**
- Produces `submit_shadow_job(main_envelope: DecisionEnvelope, text: str, filename: str, file_path: str) -> str | None`。
- Produces Celery task `tasks.execute_metadata_shadow(shadow_job_id: str)`，payload 只包含 job id。
- `shadow_route()` remains zero-LLM and returns `CascadeDecision | None` for compatibility。

- [ ] **Step 1: Write failing tests proving shadow does not block the main build.**

```python
import time

import pytest

from backend.rag.indexing.stages.metadata_stage import MetadataStage
from backend.rag.preprocessing import metadata_shadow


TEXT = "本合同由甲方与乙方签订。第一条：服务范围。"
META = {"source_file": "unknown.docx", "file_path": "", "doc_id": "shadow-test-1"}


class _EmptyRegistry:
    def list_all(self):
        return {}


class _StageEmbedding:
    model_name = "metadata-shadow-test"


@pytest.fixture
def stage():
    return MetadataStage(_EmptyRegistry(), _StageEmbedding(), department="test")


@pytest.mark.asyncio
async def test_main_metadata_result_returns_without_waiting_for_shadow(monkeypatch, stage):

    def slow_submit(*args, **kwargs):
        time.sleep(1)

    monkeypatch.setattr(metadata_shadow, "submit_shadow_job", slow_submit)
    t0 = time.monotonic()
    result = await stage.build(TEXT, META)
    elapsed = time.monotonic() - t0
    assert result["doc_type"]
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_shadow_dispatch_failure_does_not_fail_primary_index(monkeypatch, stage):
    monkeypatch.setattr(metadata_shadow, "submit_shadow_job", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("broker down")))
    result = await stage.build(TEXT, META)
    assert result["doc_type"]
```

- [ ] **Step 2: Run the shadow tests and verify they fail because current `_run_cascade_shadow` is awaited in `MetadataStage.build()`.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_metadata_shadow.py backend/tests/rag/test_metadata_stage_cascade.py -q --no-cov`

Expected: FAIL on the elapsed-time assertion or missing shadow job module.

- [ ] **Step 3: Add the shadow job table and bounded input staging.**

`026_metadata_shadow_jobs.sql` must create an `ai.metadata_shadow_jobs` table with `id`, `upload_id`, `doc_id`, `text_hash`, `filename`, `file_path`, `main_envelope_json`, `input_cache_key`, `status`, `attempts`, `error`, `created_at`, `started_at`, `finished_at`, and a unique key on `(text_hash, taxonomy_version, rules_version, model_version)`. Store only a sampled text payload capped by `METADATA_LLM_EXTRACT_MAX_CHARS` in the named cache; do not put full 50MB documents in Celery or Redis payloads.

- [ ] **Step 4: Implement non-blocking dispatch and worker execution.**

`submit_shadow_job()` writes the job row and sampled input, then pushes `execute_metadata_shadow.apply_async(args=[job_id], queue="rag_metadata_shadow")` through a bounded dispatcher. A full dispatcher, Redis miss or broker failure marks the shadow job skipped and returns `None`; it must not raise to `MetadataStage`. The Celery worker loads the input, calls `shadow_route()` under the shadow limiter, stores `shadow_doc_type`, `shadow_level`, `main_doc_type`, `agreement` and latency, and marks the job terminal. The worker never invokes R2 LLM.

- [ ] **Step 5: Register the isolated queue and remove the awaited main-path call.**

Add `tasks.execute_metadata_shadow: {"queue": "rag_metadata_shadow"}` to `celery_app.conf.task_routes`. Preserve `rag_index` worker behavior. Replace `await self._run_cascade_shadow(full_text, fname, fpath, unified, parent_span_id)` with a non-blocking `submit_shadow_job(main_envelope, full_text, fname, fpath)` call after the primary envelope is finalized. Delete the old inline embedding wait from the primary path. Keep the old helper as a compatibility wrapper only if existing tests/importers need it, but it must delegate to the dispatcher.

- [ ] **Step 6: Make shadow comparison diagnostic-only.**

Record incumbent agreement as `shadow_agreement` for diagnosis, but do not use it as a release accuracy metric. The release evaluator must compare predictions to human/gold labels. Add metrics for shadow job submitted/skipped/succeeded/failed, queue age and shadow P95.

- [ ] **Step 7: Run shadow and Celery routing tests.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_metadata_shadow.py backend/tests/rag/test_metadata_stage_cascade.py backend/tests/test_rag_upload_celery_mode.py -q --no-cov`

Expected: PASS; the main build returns without waiting for embedding shadow work, and a shadow failure never changes the primary metadata result.

- [ ] **Step 8: Commit the shadow isolation change.**

```bash
git add backend/rag/preprocessing/metadata_shadow.py backend/tasks/metadata_shadow_tasks.py backend/sql/migrations/026_metadata_shadow_jobs.sql backend/tasks/celery_app.py backend/config/tasks.py backend/rag/indexing/stages/metadata_stage.py backend/rag/preprocessing/metadata_router.py backend/observability/metrics.py backend/tests/rag/test_metadata_shadow.py backend/tests/rag/test_metadata_stage_cascade.py backend/tests/test_rag_upload_celery_mode.py
git commit -m "feat: 将元数据影子评估移出主索引路径"
```

---

### Task 7: 动态规则版本、审批、回滚和管理员 API

**Files:**
- Create: `backend/sql/migrations/025_metadata_rule_governance.sql`
- Create: `backend/rag/preprocessing/metadata_rule_service.py`
- Modify: `backend/rag/preprocessing/keyword_store_pg.py`
- Modify: `backend/rag/preprocessing/keyword_store.py`
- Modify: `backend/app/api/routes/keyword_routes.py`
- Modify: `frontend-admin/src/api/keyword.ts`
- Modify: `frontend-admin/src/app/knowledge/keywords/page.tsx`
- Create: `backend/tests/rag/test_metadata_rule_governance.py`
- Create: `backend/tests/api/test_metadata_keyword_governance_api.py`

**Interfaces:**
- Produces `create_rule_draft(entries, actor, reason) -> RuleSnapshot`。
- Produces `publish_rule_snapshot(snapshot_id, approval_id, actor) -> RuleSnapshot`。
- Produces `rollback_rule_snapshot(target_version, actor) -> RuleSnapshot`。
- Existing `get_rules_by_doc_type()` reads the active immutable snapshot and returns the old `{doc_type: [(keyword, weight)]}` shape。

- [ ] **Step 1: Write failing store/service tests.**

```python
def test_unknown_doc_type_is_rejected_before_insert(rule_service):
    with pytest.raises(ValueError, match="doc_type"):
        rule_service.create_rule_draft(
            [{"keyword": "新词", "doc_type": "not_in_taxonomy", "weight": 3}],
            actor="admin-1", reason="test")


def test_weight_is_bounded(rule_service):
    with pytest.raises(ValueError, match="weight"):
        rule_service.create_rule_draft(
            [{"keyword": "词", "doc_type": "legal", "weight": 11}],
            actor="admin-1", reason="test")


def test_unapproved_snapshot_cannot_become_active(rule_service):
    draft = rule_service.create_rule_draft(
        [{"keyword": "新词", "doc_type": "legal", "weight": 3}],
        actor="admin-1", reason="test")
    with pytest.raises(PermissionError):
        rule_service.publish_rule_snapshot(draft.version, "missing-approval", "admin-1")
```

- [ ] **Step 2: Run governance tests and verify they fail because the version tables and service do not exist.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_metadata_rule_governance.py backend/tests/api/test_metadata_keyword_governance_api.py -q --no-cov`

Expected: FAIL on missing migration/service and currently permissive `upsert()` behavior.

- [ ] **Step 3: Create immutable rule snapshot tables and backfill the current active rules.**

`025_metadata_rule_governance.sql` must create a version table with `version`, `status` (`draft/published/rolled_back`), `taxonomy_version`, `rules_hash`, `actor`, `reason`, `approval_id`, `approved_by`, `effective_at`, `created_at`, and an entries table with `version`, `keyword`, `doc_type`, `category`, `weight`, `enabled`. Add constraints for non-empty keyword, weight range 1–10 and unique `(version, keyword)`. Backfill one published snapshot from the existing table so deployment does not change current routing before a new approved snapshot exists.

- [ ] **Step 4: Implement service-level validation and snapshot reads.**

All write paths must call TaxonomySpec normalization, reject unknown labels, validate weight and keyword length, calculate a canonical snapshot hash, and record actor/reason. Reads use the published snapshot and cache it by `rules_hash`; cache invalidation occurs only after publish/rollback. A failed database/cache read returns the last in-process snapshot and emits a metric; it never silently loads arbitrary unversioned rows.

- [ ] **Step 5: Change the API to draft/publish/rollback while preserving read compatibility.**

Keep `GET /rag/keywords`, `/doc-types` and `/categories` response shapes. Change write operations to create a draft and return `status`, `version`, `rules_hash` and `review_required`; add `POST /rag/keywords/versions/{version}/publish` and `POST /rag/keywords/versions/{version}/rollback`. Require the existing administrator dependency, require `reason`, and require an existing approved `approval_id` for publish. Update the frontend to show “待审核/已发布/已回滚” instead of assuming an upsert is immediately active.

- [ ] **Step 6: Add governance regression tests.**

Test draft isolation, approval requirement, rollback restoring the previous hash, unknown doc_type rejection, weight bounds, concurrent publish serialization, active snapshot cache invalidation and API authorization. Verify old read consumers receive exactly the old keyword mapping shape.

- [ ] **Step 7: Run store, API and registry tests.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_metadata_rule_governance.py backend/tests/api/test_metadata_keyword_governance_api.py backend/tests/rag/test_rag_stores_pg.py backend/tests/rag/test_rule_freeze.py -q --no-cov`

Expected: PASS; unapproved or unknown dynamic rules cannot enter the active routing snapshot.

- [ ] **Step 8: Commit the governance change.**

```bash
git add backend/sql/migrations/025_metadata_rule_governance.sql backend/rag/preprocessing/metadata_rule_service.py backend/rag/preprocessing/keyword_store_pg.py backend/rag/preprocessing/keyword_store.py backend/app/api/routes/keyword_routes.py frontend-admin/src/api/keyword.ts frontend-admin/src/app/knowledge/keywords/page.tsx backend/tests/rag/test_metadata_rule_governance.py backend/tests/api/test_metadata_keyword_governance_api.py
git commit -m "feat: 增加元数据规则版本审批与回滚"
```

---

### Task 8: 补齐黄金集评估、分类器训练报告和发布门禁

**Files:**
- Modify: `backend/eval/metadata_baseline/predict.py`
- Modify: `backend/eval/metadata_baseline/evaluate.py`
- Modify: `backend/eval/metadata_baseline/train_lr.py`
- Create: `backend/eval/metadata_baseline/validate_release.py`
- Create: `backend/tests/eval/test_metadata_release_gates.py`
- Modify: `backend/tests/eval/test_metadata_baseline_eval.py`
- Modify: `docs/2026-09-19-RAG元数据管道统一抽取与级联路由上线规划.md`

**Interfaces:**
- `predict.py` 输出每条样本的 `route_source`、`decision`、`confidence`、`candidates`、`latency_ms`、版本字段。
- `evaluate()` 输出 `precision/recall/macro_f1/coverage/abstain_rate/confusion/calibration/risk_recall/latency/cost`。
- `build_release_report(gold_rows, prediction_rows)` 从 `prediction_rows[].incumbent_pred` 计算诊断一致率，但不把它纳入准确率门禁。
- `validate_release.py` 返回退出码 0/1，并输出每项门禁的实际值、阈值、结果和阻断原因。

- [ ] **Step 1: Write failing release-gate tests.**

```python
def test_incumbent_agreement_is_not_accuracy_gate():
    gold_rows = [{"id": "1", "doc_type_gold": "legal"}]
    prediction_rows = [{"id": "1", "pred": {"doc_type": "policy", "decision": "accepted", "confidence": 0.9}, "incumbent_pred": "policy"}]
    report = build_release_report(gold_rows, prediction_rows)
    assert report["accuracy"] == 0.0
    assert report["incumbent_agreement"] == 1.0
    assert report["gates"]["accuracy"]["passed"] is False


def test_r0_precision_gate_blocks_when_one_of_200_is_wrong():
    gold_rows = [{"id": "1", "doc_type_gold": "legal"} for _ in range(199)] + [{"id": "200", "doc_type_gold": "legal"}]
    prediction_rows = [{"id": str(i), "pred": {"doc_type": "legal", "route_source": "r0", "confidence": 0.999}}
                       for i in range(199)] + [{"id": "200", "pred": {"doc_type": "policy", "route_source": "r0", "confidence": 0.999}}]
    report = build_release_report(gold_rows, prediction_rows)
    assert report["gates"]["r0_precision"]["passed"] is False


def test_abstain_counts_as_coverage_loss_not_silent_general():
    gold_rows = [{"id": "1", "doc_type_gold": "legal"}]
    prediction_rows = [{"id": "1", "pred": {"doc_type": "general", "decision": "abstain", "confidence": 0.5}}]
    report = build_release_report(gold_rows, prediction_rows)
    assert report["coverage"] == 0.0
    assert report["abstain_rate"] == 1.0
```

- [ ] **Step 2: Run the release-gate tests and verify they fail because the new metrics do not exist.**

Run: `D:/Python/python.exe -m pytest backend/tests/eval/test_metadata_release_gates.py backend/tests/eval/test_metadata_baseline_eval.py -q --no-cov`

Expected: FAIL on missing coverage/calibration/gate fields.

- [ ] **Step 3: Fix prediction replay so cascade evaluation actually exercises R1.**

When `predict.py --route cascade` is selected, load the configured embedding and pass it to the decision router. Do not pass `embedding=None` for the normal cascade path. Persist `route_source`, `decision`, `candidates`, `evidence`, `abstain_reason`, `taxonomy_version`, `rules_version`, `model_version` and `prompt_version` in each JSONL prediction. Keep a separate `--route legacy_rule` for historical comparison.

- [ ] **Step 4: Add formal metrics and label requirements.**

Extend `evaluate.py` with:

```python
def coverage(rows: list[dict]) -> float:
    raise NotImplementedError


def abstain_rate(rows: list[dict]) -> float:
    raise NotImplementedError


def expected_calibration_error(rows: list[dict], bins: int = 10) -> float:
    raise NotImplementedError


def build_release_report(gold_rows: list[dict], prediction_rows: list[dict]) -> dict:
    raise NotImplementedError
```

Gold validation must require `id`, `text`, `doc_type_gold`, double annotator fields or an explicit adjudication record, and enough support per label. Separate filename-signal ablation and cross-document-family split reports must be emitted. Existing LLM agreement remains a diagnostic field only.

- [ ] **Step 5: Implement exact release gates.**

`validate_release.py` must enforce:

```text
R0 precision >= 0.995
R1 per-class precision >= 0.98
R2 schema pass rate >= 0.995
fallback metadata LLM calls == 0
high-risk recall >= 0.95
shadow does not increase primary P95
2x expected peak load has no sustained queue growth
rollback completes within 10 minutes
```

The command must fail closed when a metric is missing, a label has fewer than 50 adjudicated samples, or the model/rules/taxonomy fingerprints differ between training and prediction. It must print a machine-readable JSON report and a concise human-readable summary.

- [ ] **Step 6: Run the evaluator tests and dry-run gate checker.**

Run: `D:/Python/python.exe -m pytest backend/tests/eval/test_metadata_release_gates.py backend/tests/eval/test_metadata_baseline_eval.py -q --no-cov`

Run: `D:/Python/python.exe -m backend.eval.metadata_baseline.validate_release --golden backend/eval/metadata_baseline/golden_sample.jsonl --pred backend/eval/metadata_baseline/preds_unified_seed.jsonl --allow-dry-run`

Expected: unit tests PASS; the dry-run command explicitly reports that the sample/seed artifact is not a production promotion and exits nonzero if formal gate fields are absent.

- [ ] **Step 7: Update the planning document with evidence, not assumptions.**

Record the actual golden-set size, per-label support, Kappa, route coverage, precision/recall/F1, ECE, risk recall, P50/P95/P99, LLM rate/cost, 2x load result and rollback drill result. Keep `METADATA_CASCADE_ENABLED=false` until every required gate passes.

- [ ] **Step 8: Commit the evaluation and gate change.**

```bash
git add backend/eval/metadata_baseline/predict.py backend/eval/metadata_baseline/evaluate.py backend/eval/metadata_baseline/train_lr.py backend/eval/metadata_baseline/validate_release.py backend/tests/eval/test_metadata_release_gates.py backend/tests/eval/test_metadata_baseline_eval.py docs/2026-09-19-RAG元数据管道统一抽取与级联路由上线规划.md
git commit -m "feat: 增加元数据黄金集评估与发布门禁"
```

---

### Task 9: 灰度开关、回滚演练和最终验证

**Files:**
- Modify: `backend/config/rag.py`
- Modify: `backend/config/tasks.py`
- Modify: `backend/observability/metrics.py`
- Modify: `backend/rag/preprocessing/metadata_rule_service.py`
- Modify: `docs/2026-09-19-RAG元数据管道统一抽取与级联路由上线规划.md`
- Modify: `docs/superpowers/specs/2026-09-19-rag-metadata-pipeline-governance-design.md`
- Create: `backend/tests/rag/test_metadata_rollout_config.py`

**Interfaces:**
- Default configuration remains safe: cascade off, classifier off, shadow dispatch independently switchable。
- Produces `rollback_metadata_route(rules_version: str, model_version: str) -> dict` and records the pointer change without deleting history。
- Rollout sequence is `1% → 10% → 50% → 100%`，each stage has an explicit stop condition。
- Rollback changes only configuration/model/rule snapshot pointers and does not delete metadata history。

- [ ] **Step 1: Write rollout configuration tests.**

```python
def test_defaults_keep_cascade_and_classifier_disabled():
    assert config.METADATA_CASCADE_ENABLED is False
    assert config.METADATA_CLASSIFIER_ENABLED is False


def test_rollout_percentage_is_bounded():
    assert config.METADATA_CASCADE_ROLLOUT_PERCENT in range(0, 101)


def test_rollback_target_is_versioned(monkeypatch):
    result = rollback_metadata_route("rules-v1", "model-v0")
    assert result["rules_version"] == "rules-v1"
    assert result["model_version"] == "model-v0"
```

- [ ] **Step 2: Run rollout tests and verify they fail for missing configuration/rollback helper.**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_metadata_rollout_config.py -q --no-cov`

Expected: FAIL before rollout fields and version-pointer helper exist.

- [ ] **Step 3: Add bounded rollout configuration and fail-closed checks.**

Add `METADATA_CASCADE_ROLLOUT_PERCENT=0`, `METADATA_CASCADE_ROLLOUT_KEY`, `METADATA_ROLLBACK_RULES_VERSION`, `METADATA_ROLLBACK_MODEL_VERSION` and `METADATA_SHADOW_QUEUE_ENABLED`. Reject percentages outside 0–100 at config load. A missing model card, rules snapshot or taxonomy fingerprint mismatch forces route source `llm`/`fallback` and cannot be overridden by percentage.

- [ ] **Step 4: Perform rollback drill against a staging task.**

Record the active taxonomy/rules/model/prompt versions, switch to the previous known-good pointers, reprocess one idempotent document, verify no duplicate effective row and verify the old fingerprint is present in the result. Measure wall-clock time; the release gate requires no more than 10 minutes.

- [ ] **Step 5: Run the complete targeted validation.**

Run:

```powershell
D:/Python/python.exe -m compileall -q backend/rag/preprocessing backend/rag/indexing/stages backend/tasks backend/eval/metadata_baseline
D:/Python/python.exe -m pytest backend/tests/rag/test_taxonomy_spec.py backend/tests/rag/test_metadata_schema.py backend/tests/rag/test_metadata_llm.py backend/tests/rag/test_metadata_evidence.py backend/tests/rag/test_metadata_classifier.py backend/tests/rag/test_metadata_decision.py backend/tests/rag/test_metadata_router.py backend/tests/rag/test_metadata_runtime.py backend/tests/rag/test_metadata_shadow.py backend/tests/rag/test_metadata_rule_governance.py backend/tests/rag/test_metadata_stage_cascade.py backend/tests/eval/test_metadata_baseline_eval.py backend/tests/eval/test_metadata_classifier_training.py backend/tests/eval/test_metadata_release_gates.py backend/tests/rag/test_rule_freeze.py -q --no-cov
D:/Python/python.exe -m pytest backend/tests/test_rag_upload_celery_mode.py backend/tests/test_rag_upload_concurrency.py backend/tests/test_upload_resilience.py -q --no-cov
git diff --check
```

Expected: compile succeeds, all targeted tests pass, upload queue tests remain green, and `git diff --check` returns no output. The known existing async-mock warning in the old shadow test must be fixed while migrating the test; warnings are not accepted as a substitute for the assertion.

- [ ] **Step 6: Run the two-times-peak load test with shadow enabled and primary cascade disabled.**

Use the existing `rag_index` worker and the new shadow worker with controlled concurrency. Capture queue length/age, worker throughput, embedding/LLM QPS, 429 rate, DB pool wait, primary/shadow P95 and duplicate-write count. Repeat with R1 enabled only after the calibrated model gate passes. Do not promote on average latency alone.

- [ ] **Step 7: Promote by staged configuration only after the gate checker returns success.**

Apply `1%`, observe the complete gate report, then `10%`, `50%`, and `100%`. At each step, immediately return to `METADATA_CASCADE_ENABLED=false` and previous model/rules pointers when any accuracy, cost, latency, queue, error or duplicate-write gate fails. Keep the unified LLM path as the safe primary fallback throughout the rollout.

- [ ] **Step 8: Commit the rollout documentation and final verification.**

```bash
git add backend/config/rag.py backend/config/tasks.py backend/observability/metrics.py docs/2026-09-19-RAG元数据管道统一抽取与级联路由上线规划.md docs/superpowers/specs/2026-09-19-rag-metadata-pipeline-governance-design.md backend/tests/rag/test_metadata_rollout_config.py
git commit -m "docs: 完成元数据管道灰度与回滚运行手册"
```

## Verification Checklist

- [ ] `DecisionEnvelope` 是所有 R0/R1/R2/fallback/human 路径的唯一决策契约。
- [ ] TaxonomySpec、Prompt、Schema、分类器和规则校验均来自同一版本化数据源。
- [ ] 普通文件名、目录词和泛关键词不能单独触发 R0。
- [ ] R1 使用校准概率、逐类阈值和 margin；原始 cosine 只作为特征/候选，不是置信度。
- [ ] R2 每个文档决策最多一次，Schema/PolicyGate 失败进入 fallback/review。
- [ ] fallback LLM 调用数为 0，并返回完整 metadata 契约。
- [ ] 影子任务不在主 `MetadataStage.build()` 上等待，不共享主路径的 Embedding/LLM 槽位。
- [ ] Celery 只接收影子 job id；主 `rag_index` 队列与影子队列可独立扩缩容。
- [ ] 动态规则未经审批不能进入活动快照，且可按版本回滚。
- [ ] 缓存键包含文本、Taxonomy、规则、模型和 Prompt 版本；Celery 重试不产生重复有效写入。
- [ ] 黄金标签评估与现网 LLM 一致率分离；切流门禁不使用 incumbent agreement 代替 accuracy。
- [ ] 2 倍峰值压测没有持续队列增长，429/错误率、DB 等待和 P95 均在批准阈值内。
- [ ] 回滚演练不超过 10 分钟，且保留历史版本和审计记录。
- [ ] 默认配置仍是 cascade off、classifier off；所有灰度动作均可单配置回退。
