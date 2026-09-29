# RAG 20k 阶段 0 基线与证据实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改 RAG 运行时行为的前提下，为 20,000 文档、多租户和高并发改造建立可复现的环境基线、决策记录、数据清单校验、500 条黄金集校验与阶段证据索引。

**Architecture:** 阶段 0 只新增审计纯函数、只读 CLI、测试和证据文档。工具读取 Git、Docker、运行时版本、显式白名单配置和外部提供的 JSONL 清单；不启动服务、不写数据库、不读取或输出密钥。现有 `rag_eval_kb` 统一工作在独立 worktree 中进行，本计划等待其合并后再执行真实 100 文档双跑，避免同时修改评测核心文件。

**Tech Stack:** Python 3.10+、标准库、Pydantic 2、现有 evaluation CLI、pytest、Git、Docker Compose、JSON/JSONL。

**Spec:** `docs/2026-09-18-架构审计报告-LangGraph-并发-RAG20000.md`

## Global Constraints

- 阶段 0 不修改 RAG 运行时代码、数据库 schema、生产配置或部署状态。
- 当前 `main` 工作区有另一条安全/幂等/预算/反馈计划的未提交改动；本计划不得修改、暂存、提交或格式化这些文件。
- `codex/rag-eval-kb-unification` worktree 已有评测统一改动；真实 100 文档双跑与 500 条黄金集接线必须等待该工作合并，不得复制其在途实现。
- 所有采集命令必须只读；禁止通过采集器启动、停止或重启 Docker 服务。
- 任何名称含 `KEY`、`TOKEN`、`SECRET`、`PASSWORD`、`CREDENTIAL` 的配置都不得写入证据文件；允许记录“是否已配置”，不允许记录值或哈希。
- 原始 20k 文档、对象存储凭据、合同和授权材料不进入 Git；仓库仅保存脱敏后的清单、哈希、统计和证据链接。
- 所有 Python 新增行为遵循 TDD；局部 pytest 必须带 `--no-cov`。
- 任何外部依赖未满足时输出结构化 `blocked`，不得用暂定假设冒充验收通过。

---

### Task 1: 建立只读基线清单采集器

**Files:**
- Create: `backend/audit/__init__.py`
- Create: `backend/audit/rag20k/__init__.py`
- Create: `backend/audit/rag20k/baseline.py`
- Create: `backend/scripts/collect_rag20k_baseline.py`
- Test: `backend/tests/audit/test_rag20k_baseline.py`
- Generate: `docs/evidence/rag20k/phase0/baseline/{capture_id}/baseline-manifest.json`

**Interfaces:**
- `collect_baseline(repo_root: Path, command_runner: CommandRunner | None = None) -> dict[str, object]`
- `write_baseline_manifest(manifest: Mapping[str, object], output_root: Path) -> Path`
- `CommandResult`: `status`, `stdout`, `stderr`, `returncode`；清单只保留版本类命令输出。
- `capture_id`：对去掉 `captured_at_utc` 后的规范 JSON 做 SHA-256，取前 16 位。

- [x] **Step 1: 写失败测试，锁定可复现性、工作树哈希和脱敏规则**

```python
def test_collect_baseline_is_stable_except_timestamp(tmp_path, fake_commands):
    first = collect_baseline(tmp_path, fake_commands)
    second = collect_baseline(tmp_path, fake_commands)
    assert first["capture_id"] == second["capture_id"]
    assert first["captured_at_utc"] != ""


def test_manifest_never_contains_secret_values(tmp_path, monkeypatch, fake_commands):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-leak")
    manifest = collect_baseline(tmp_path, fake_commands)
    serialized = json.dumps(manifest, ensure_ascii=False)
    assert "must-not-leak" not in serialized
    assert manifest["configuration"]["DEEPSEEK_API_KEY"] == {"configured": True}


def test_dirty_tree_fingerprint_includes_untracked_files(tmp_path, git_repo):
    (tmp_path / "new.txt").write_text("v1", encoding="utf-8")
    first = collect_baseline(tmp_path)["git"]["workspace_sha256"]
    (tmp_path / "new.txt").write_text("v2", encoding="utf-8")
    second = collect_baseline(tmp_path)["git"]["workspace_sha256"]
    assert first != second
```

- [x] **Step 2: 运行测试并确认 RED**

Run: `D:/Python/python.exe -m pytest backend/tests/audit/test_rag20k_baseline.py -q --no-cov`

Expected: FAIL，原因是 `backend.audit.rag20k.baseline` 尚不存在。

- [x] **Step 3: 实现纯函数采集器和只读 CLI**

采集器必须包含以下字段：

```json
{
  "schema_version": "1.0",
  "capture_id": "0123456789abcdef",
  "captured_at_utc": "2026-09-19T00:00:00Z",
  "git": {
    "commit": "0000000000000000000000000000000000000000",
    "branch": "main",
    "dirty": true,
    "tracked_diff_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "untracked_manifest_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "workspace_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "changed_path_count": 0,
    "untracked_path_count": 0
  },
  "runtime": {},
  "dependencies": {},
  "docker": {"status": "ok|blocked", "images": []},
  "configuration": {},
  "blocking_items": []
}
```

工作树指纹由 `git diff --binary HEAD` 与排序后的未跟踪文件相对路径/内容 SHA-256 共同计算；不得把 diff 正文写入 JSON。配置只允许显式白名单：模型名、Embedding 维度/批大小、切分、候选池、HNSW、Rerank、MultiQuery、Evidence Gate、连接池和 RAG 模式；密钥字段只记录 `configured`。Docker 不可用、镜像无 RepoDigest、命令缺失时加入 `blocking_items`，采集仍成功退出并明确状态。

- [x] **Step 4: 运行测试确认 GREEN，并做语法检查**

Run: `D:/Python/python.exe -m pytest backend/tests/audit/test_rag20k_baseline.py -q --no-cov`

Run: `D:/Python/python.exe -m py_compile backend/audit/rag20k/baseline.py backend/scripts/collect_rag20k_baseline.py`

Expected: 两条命令均退出 0。

- [x] **Step 5: 采集当前工作树基线**

Run: `D:/Python/python.exe backend/scripts/collect_rag20k_baseline.py --repo-root . --output-root docs/evidence/rag20k/phase0/baseline`

Expected: 输出一个带 `capture_id` 的目录；JSON 不含密钥值；当前工作树 `dirty=true`；Docker 或镜像 digest 不满足时写入阻断项，不伪造值。

- [x] **Step 6: 审核范围并提交**

Run: `git diff -- backend/audit backend/scripts/collect_rag20k_baseline.py backend/tests/audit docs/evidence/rag20k docs/superpowers/plans/2026-09-19-rag20k-phase0-baseline.md`

Expected: 仅包含本任务新文件和生成证据。

Commit: `feat(audit): add reproducible rag20k baseline capture`

## 执行记录（2026-09-19）

**Task 1 完成时的采集器缺陷修复**（均为 TDD，先固化失败测试再修）：

1. **指纹自引用**：证据输出目录是未跟踪目录，第一次采集的产物会进入第二次采集的工作树指纹，导致相同环境产生不同 capture_id。修复：`collect_git_state` 默认把 `docs/evidence/rag20k/phase0/baseline` 下的未跟踪文件排除出指纹与 dirty 判定（已提交的证据经 commit 哈希照常参与），排除策略写入 `git.fingerprint_excludes` 以便事后复算 ID。业务代码、计划、测试等其他未跟踪文件仍全部参与指纹。
2. **porcelain 目录折叠**：`git status --porcelain=v1 -z` 会把整个未跟踪目录折叠成一条 `?? docs/`，按前缀排除会把同目录下其他未跟踪文件一起误伤。修复：改用 `--untracked-files=all` 逐文件列出。
3. 更早一轮已修复并经回归测试锁定：Compose v5 字段映射（`ContainerName` + `docker image inspect` 取 RepoDigest）、Windows `npm.cmd` 解析、PG/Redis 版本取运行容器而非宿主客户端。

**失效证据处置**：采集器修复前生成的两份清单已删除，其 capture_id 仅在此留档，不得作为基线引用：

- `85a0067566c869d2`（12 镜像折叠为 unknown、npm 误报不可用）
- `005c6543e402b55b`（内容正确，但 capture_id 由自引用指纹算法计算，不可复现）

**有效基线**：`docs/evidence/rag20k/phase0/baseline/8ad9e77f1b4fd9f4/baseline-manifest.json`，阻断项 0；连续两次采集 capture_id 一致（幂等验证通过）；12 镜像均有 RepoDigest；PG 16.14 / Redis 7.4.9（运行容器实测版本）。（Task 5 增补 2026-09-19：Step 1-2 比较器已完成并合入 main；worktree 已收尾合并（022 迁移 renumber 为 024）。**Step 3 实证修正**：cloud 模式答案生成需显式 `--judge` 开启（runner 中 `_allow_cloud = judge or ragas`），锁定命令据此增补该开关；rag_eval_kb/expanded_100 已入库（95 active + 1 failed：table_customer_satisfaction_2026 夹具过薄触发 ChunkingEmptyError，如实留档），迁移副本 near-dup 缺陷已修（d7a31cd，95 条知情激活留痕）。索引指纹 `ac633326d7c41bfe`。run-1 已完成且有效（git 2f2ef44，169 case，62 拒答 + 107 有答案）；run-2 两次尝试均失效——第一次并行会话中途提交（R11 应验，口径不一致被比较器正确拒绝），第二次 DashScope 账户欠费（Arrearage，400 拒绝）。**待解阻**：① DashScope 充值；② 约 25 分钟无提交窗口后背靠背重跑 run-2 + 比较。）

### Task 2: 冻结 Q1—Q10 决策记录与签字状态

**Files:**
- Create: `docs/evidence/rag20k/phase0/decisions/Q1-Q10.md`
- Create: `docs/evidence/rag20k/phase0/decisions/Q1-Q10.json`
- Create: `backend/audit/rag20k/decisions.py`
- Create: `backend/scripts/validate_rag20k_decisions.py`
- Test: `backend/tests/audit/test_rag20k_decisions.py`

**Interfaces:**
- `validate_decisions(data: Mapping[str, object]) -> list[ValidationIssue]`
- 每项必须有 `id`、`decision`、`owner`、`decided_at`、`evidence_refs`、`status`。
- `status` 只允许 `confirmed` 或 `blocked`；`blocked` 必须包含 `blocking_reason`，且整个阶段 0 不通过。

- [x] **Step 1: 写失败测试，拒绝空 owner、无日期、无证据和模糊数值**

```python
def test_decisions_require_owner_date_and_evidence():
    issues = validate_decisions({"decisions": [{"id": "Q1", "status": "confirmed"}]})
    assert {issue.field for issue in issues} >= {"owner", "decided_at", "evidence_refs"}


def test_blocked_decision_prevents_phase0_pass():
    result = summarize_decisions(load_fixture("q1_q10_with_q6_blocked.json"))
    assert result.phase0_ready is False
    assert result.blocked_ids == ["Q6"]
```

- [x] **Step 2: 运行测试确认 RED**

Run: `D:/Python/python.exe -m pytest backend/tests/audit/test_rag20k_decisions.py -q --no-cov`

Expected: FAIL，决策校验模块不存在。

- [x] **Step 3: 实现校验器并录入审计报告现有事实**

Q1—Q5、Q7—Q10 只能在负责人、日期和证据齐全时标记 `confirmed`；未取得供应商书面配额的 Q6 必须标记 `blocked`，决策写明“Embedding 并发固定为 1，不承诺 8 小时导入”。不得把审计报告的暂定假设直接标记成已签字结论。

- [x] **Step 4: 运行验证并生成机器可读摘要**

Run: `D:/Python/python.exe backend/scripts/validate_rag20k_decisions.py docs/evidence/rag20k/phase0/decisions/Q1-Q10.json`

Expected: 在仍有 `blocked` 项时退出 2，并打印精确阻断 ID；schema 错误退出 1；全部确认退出 0。

- [x] **Step 5: 提交决策契约**

Commit: `docs(audit): freeze rag20k phase0 decisions`

### Task 3: 建立 20k 文档清单校验器

**Files:**
- Create: `backend/audit/rag20k/corpus_manifest.py`
- Create: `backend/scripts/validate_rag20k_corpus.py`
- Create: `backend/tests/audit/fixtures/rag20k_corpus_valid.jsonl`
- Create: `backend/tests/audit/test_rag20k_corpus_manifest.py`
- Generate: `docs/evidence/rag20k/phase0/corpus/validation-summary.json`

**Interfaces:**
- `validate_corpus_manifest(path: Path, expected_count: int = 20_000) -> CorpusValidationResult`
- 复合唯一键：`tenant_id + kb_id + doc_id + version`。
- 必填字段：`tenant_id`、`kb_id`、`doc_id`、`version`、`format`、`size_bytes`、`permission_scope`、`language`、`is_ocr`、`content_sha256`、`object_uri`、`authorization_ref`。

- [x] **Step 1: 写失败测试，覆盖数量、空值、重复键、格式和 URI**

```python
def test_manifest_rejects_duplicate_compound_key(tmp_path):
    path = write_jsonl(tmp_path, [valid_row(doc_id="same"), valid_row(doc_id="same")])
    result = validate_corpus_manifest(path, expected_count=2)
    assert result.valid is False
    assert result.duplicate_key_count == 1


def test_manifest_never_reads_document_body(tmp_path):
    path = write_jsonl(tmp_path, [valid_row(object_uri="s3://private/doc.pdf")])
    result = validate_corpus_manifest(path, expected_count=1)
    assert result.uri_scheme_counts == {"s3": 1}
```

- [x] **Step 2: 运行测试确认 RED**

Run: `D:/Python/python.exe -m pytest backend/tests/audit/test_rag20k_corpus_manifest.py -q --no-cov`

- [x] **Step 3: 实现流式 JSONL 校验与脱敏统计**

校验器逐行读取，不把 20k 行全部载入内存；输出只包含数量、格式分布、大小分位数、OCR/语言/权限分布、重复键的不可逆哈希和错误行号。`object_uri` 只验证 scheme 与非空，不把完整 URI复制到摘要。未知扩展名、PPT/PPTX/HTML 按审计决策输出明确错误或阻断。

- [x] **Step 4: 运行测试与真实清单验证**

Run: `D:/Python/python.exe -m pytest backend/tests/audit/test_rag20k_corpus_manifest.py -q --no-cov`

Run: `D:/Python/python.exe backend/scripts/validate_rag20k_corpus.py D:/rag20k/manifests/corpus-20000.jsonl --expected-count 20000 --output docs/evidence/rag20k/phase0/corpus/validation-summary.json`

Expected: 未提供真实清单时明确 `blocked`，不得生成伪造的 20k 数据。

- [x] **Step 5: 提交校验器与脱敏摘要**

Commit: `feat(audit): validate rag20k corpus manifest`

### Task 4: 建立 500 条互斥黄金查询校验器

**Files:**
- Create: `backend/audit/rag20k/golden_manifest.py`
- Create: `backend/scripts/validate_rag20k_golden.py`
- Create: `backend/tests/audit/fixtures/rag20k_golden_valid.jsonl`
- Create: `backend/tests/audit/test_rag20k_golden_manifest.py`
- Generate: `docs/evidence/rag20k/phase0/golden/validation-summary.json`

**Interfaces:**
- `validate_golden_manifest(path: Path) -> GoldenValidationResult`
- 九类固定数量：FAQ 100、精确定位 80、表格 80、多条件 70、跨文档 60、权限 40、版本 30、无依据 20、OCR 20。
- 每条必须有 `case_id`、`category`、`question`、`tenant_id`、`kb_id`、`permission_context`、`expected_doc_ids`、`expected_chunk_ids`、`should_reject`、`annotation_status`、`reviewers`。

- [x] **Step 1: 写失败测试，覆盖互斥分类、数量和双审比例**

```python
def test_category_distribution_is_exact():
    result = validate_golden_manifest(fixture_path("golden_500.jsonl"))
    assert result.category_counts == EXPECTED_CATEGORY_COUNTS


def test_second_review_covers_at_least_twenty_percent():
    result = validate_golden_manifest(fixture_path("golden_under_reviewed.jsonl"))
    assert result.valid is False
    assert result.second_review_ratio < 0.20
```

- [x] **Step 2: 运行测试确认 RED**

Run: `D:/Python/python.exe -m pytest backend/tests/audit/test_rag20k_golden_manifest.py -q --no-cov`

- [x] **Step 3: 实现校验器并保持与统一评测 KB 解耦**

该任务只校验外部黄金清单，不修改 `backend/evaluation`。等 `codex/rag-eval-kb-unification` 合并后，再在后续实施计划中做导入适配。权限、版本和无依据用例的期望字段必须互斥且可机器判断；分歧未裁决的条目使阶段 0 失败。

- [x] **Step 4: 运行测试与真实黄金集验证**

Run: `D:/Python/python.exe -m pytest backend/tests/audit/test_rag20k_golden_manifest.py -q --no-cov`

Run: `D:/Python/python.exe backend/scripts/validate_rag20k_golden.py D:/rag20k/manifests/golden-500.jsonl --output docs/evidence/rag20k/phase0/golden/validation-summary.json`

Expected: 未提供 500 条真实标注时输出 `blocked`，不扩写合成答案冒充业务标注。

- [x] **Step 5: 提交校验器与脱敏摘要**

Commit: `feat(audit): validate rag20k golden manifest`

### Task 5: 复跑 100 文档基线并校验两次差值

**Files:**
- Create: `backend/audit/rag20k/eval_reproducibility.py`
- Create: `backend/scripts/compare_rag_baselines.py`
- Create: `backend/tests/audit/test_rag_baseline_reproducibility.py`
- Generate: `docs/evidence/rag20k/phase0/evaluation/{capture_id}/summary.json`

**Interfaces:**
- `compare_eval_runs(first: Mapping[str, object], second: Mapping[str, object], tolerance: float = 0.005) -> ReproducibilityResult`
- 必比主指标：`recall@5`、`mrr`、`ndcg@10`、`top1_accuracy`、`citation_accuracy`、`reject_accuracy`。

- [x] **Step 1: 写失败测试，拒绝口径不一致和差值超限**

```python
def test_compare_rejects_different_dataset_or_config_fingerprint():
    result = compare_eval_runs(run(dataset="v1"), run(dataset="v2"))
    assert result.comparable is False


def test_compare_requires_all_primary_metrics_within_0005():
    result = compare_eval_runs(run(recall=0.900), run(recall=0.906))
    assert result.passed is False
    assert result.metric_deltas["recall@5"] == pytest.approx(0.006)
```

- [x] **Step 2: 运行测试确认 RED，随后实现比较器**

Run: `D:/Python/python.exe -m pytest backend/tests/audit/test_rag_baseline_reproducibility.py -q --no-cov`

- [ ] **Step 3: 等统一评测 KB 合并后执行两次真实评测**

Run twice from repository root:

```powershell
D:/Python/python.exe -m backend.evaluation rag --selection expanded_100 --multiquery --no-ragas --no-resume --output docs/evidence/rag20k/phase0/evaluation/run-1
D:/Python/python.exe -m backend.evaluation rag --selection expanded_100 --multiquery --no-ragas --no-resume --output docs/evidence/rag20k/phase0/evaluation/run-2
```

若合并后的 CLI 入口仍要求在 `backend` 目录运行，计划执行者必须先以 `python -m backend.evaluation --help` 的退出码确认入口，不得猜测命令。两次运行必须使用相同 commit、数据版本、模型、Embedding、配置指纹和索引版本。

- [ ] **Step 4: 比较并生成结论**

Run: `D:/Python/python.exe backend/scripts/compare_rag_baselines.py docs/evidence/rag20k/phase0/evaluation/run-1/report.json docs/evidence/rag20k/phase0/evaluation/run-2/report.json --max-delta 0.005 --output docs/evidence/rag20k/phase0/evaluation/reproducibility.json`

Expected: 所有主指标差值绝对值 ≤ 0.005；任何缺失指标或口径差异都失败。

- [ ] **Step 5: 提交比较器与可复现证据**

Commit: `test(audit): freeze reproducible 100-doc rag baseline`

### Task 6: 建立风险登记册与阶段 0 出口门

**Files:**
- Create: `docs/evidence/rag20k/phase0/risk-register.md`
- Create: `docs/evidence/rag20k/phase0/README.md`
- Create: `backend/audit/rag20k/phase_gate.py`
- Create: `backend/scripts/check_rag20k_phase0.py`
- Test: `backend/tests/audit/test_rag20k_phase_gate.py`
- Modify: `docs/2026-09-18-架构审计报告-LangGraph-并发-RAG20000.md`

**Interfaces:**
- `evaluate_phase0(evidence_root: Path) -> PhaseGateResult`
- 出口门只接受 0.1—0.6 六项机器可读证据均通过；签字类证据必须保留 owner/date/ref。

- [x] **Step 1: 写失败测试，缺任一证据均不得通过**

```python
def test_phase0_gate_lists_missing_evidence(tmp_path):
    result = evaluate_phase0(tmp_path)
    assert result.passed is False
    assert set(result.missing) == {
        "baseline", "decisions", "corpus", "golden", "evaluation", "risk_register"
    }
```

- [x] **Step 2: 运行测试确认 RED，随后实现出口门**

Run: `D:/Python/python.exe -m pytest backend/tests/audit/test_rag20k_phase_gate.py -q --no-cov`

- [x] **Step 3: 写风险登记册与证据索引**

每个 P0/P1 风险必须有 `owner`、`due_date`、`trigger`、`mitigation`、`rollback`、`evidence_ref` 和 `status`。`README.md` 列出一条命令重建/校验每项证据；外部材料只链接，不复制敏感正文。

- [x] **Step 4: 执行阶段 0 出口检查**

Run: `D:/Python/python.exe backend/scripts/check_rag20k_phase0.py docs/evidence/rag20k/phase0`

Expected: 证据未齐时退出 2 并列出缺口；schema 错误退出 1；全部通过退出 0。

- [x] **Step 5: 更新审计报告进度，不虚报外部验收**

在报告顶部增加“实施进度”表，分别记录 0.1—0.6 的状态、证据路径和阻断项；只有出口命令退出 0 才勾选阶段 0 对应验收框。当前外部配额、20k 真实清单、500 条双审标注未提供时保持未完成。

- [x] **Step 6: 运行阶段 0 完整验证**

Run:

```powershell
D:/Python/python.exe -m pytest backend/tests/audit -q --no-cov
D:/Python/python.exe -m compileall -q backend/audit backend/scripts
D:/Python/python.exe backend/scripts/check_rag20k_phase0.py docs/evidence/rag20k/phase0
git diff --check
```

Expected: 测试和编译退出 0；出口检查只有在真实外部证据齐全时退出 0，否则以明确阻断状态结束；`git diff --check` 退出 0。

Commit: `docs(audit): record rag20k phase0 gate evidence`

## 执行顺序与检查点

1. Task 1 可立即执行，并冻结当前 175 项在途改动的可追踪基线。
2. Task 2 可立即建立契约，但 Q1—Q10 的确认状态必须由对应负责人和证据决定。
3. Task 3、Task 4 可先实现校验器；真实验收等待外部 20k 清单和 500 条标注。
4. Task 5 必须等待 `codex/rag-eval-kb-unification` 合并且定向回归通过。
5. Task 6 汇总出口门；阶段 0 未通过前，不执行审计报告阶段 1 的 tenant schema、Celery 拆分或 BM25 迁移。
