import { request } from "@/lib/fetcher";
import { apiErrorFromEnvelope, fetchRaw } from "@/api/client";

const BASE = "/api/evaluation";

export interface EvalCase {
  id: string;
  question: string;
  module: string;
  expected: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

export interface AppendResult {
  appended: boolean;
  reason: string;
  case_id: string;
  path: string;
}

export interface FromTracePayload {
  trace_id: string;
  module?: string;
  expected?: Record<string, unknown>;
  note?: string;
}

export interface RunEvalResult {
  ok: boolean;
  total: number;
  passed: number;
  failed: number;
  pass_rate: number;
  top1_accuracy: number;
  reject_accuracy: number;
  recall_at_5: number;
  timestamp: string;
  error?: string;
  run_id?: string;
  suite?: string;
  evaluation_mode?: string;
  evaluator_mode?: string;
  validity?: string;
}

export type EvaluationMode = 'offline' | 'semantic' | 'ragas' | 'self+ragas'

export interface RunSummary {
  run_id: string;
  module: string;
  pass_rate: number;
  top1_accuracy: number;
  faithfulness: number;
  answer_correctness: number;
  recall_at_5: number;
  reject_accuracy: number;
  mrr: number;
  ndcg_at_10: number;
  timestamp: string;
  /** RUN-01：running/completed/failed（旧 run 可能缺状态文件 = 空） */
  status?: string;
  /** RUN-07/08：running 超过 stale 阈值（worker 丢失/卡死嫌疑） */
  stale?: boolean;
  /** UI-01/02：评测集溯源与触发来源（来自 meta.eval_provenance / meta.env） */
  suite?: string;
  dataset_version?: string;
  trigger?: string;
  triggered_by?: string;
  /** UI-04：RAGAS 徽标数据源——self=未跑 RAGAS，self+ragas=双轨 */
  evaluator_mode?: string;
  /** P0-04：run 有效性——INVALID_* 的 run 显示「环境无效」而非红色 0% */
  validity?: string;
  invalid_reason?: string;
  case_count?: number;
  pass_count?: number;
  /** Phase 2：列表摘要来源（postgres=权威 / file_fallback=PG 不可用降级） */
  summary_source?: string;
}

export interface DatasetCatalogItem {
  dataset_id: string;
  module: string;
  /** core=常规质量评测集；probe=需 --live 的探针集（旧后端不返回，前端按 core 兜底） */
  kind?: 'core' | 'probe';
  dataset_version: string;
  owner: string;
  review_status: string;
  case_count: number;
  content_hash: string;
  coverage: {
    tiers?: Record<string, number>;
    sources?: Record<string, number>;
    query_types?: Record<string, number>;
    verified_count?: number;
  };
  kb_id: string;
  fixture_set: string;
  latest_run?: Record<string, unknown> | null;
}

export interface DatasetCandidate {
  candidate_id: string;
  module: string;
  question: string;
  expected: Record<string, unknown>;
  metadata: Record<string, unknown>;
  source_type: string;
  redacted: boolean;
  owner: string;
  status: string;
  dataset_version: string;
  created_at: string;
  reviewer: string;
  review_reason: string;
  approved_version: string;
  content_hash: string;
}

export interface DatasetVersionDetail {
  dataset_id: string;
  version: string;
  immutable: boolean;
  owner: string;
  review_status: string;
  case_count: number;
  content_hash: string;
  metadata: Record<string, unknown>;
  coverage: Record<string, unknown>;
  case_diff: { added: string[]; removed: string[]; changed: string[] };
  suite_membership: Array<Record<string, unknown>>;
  audit: Array<Record<string, unknown>>;
}

export interface EvaluationV2Dataset {
  dataset_id: string;
  dataset_key: string;
  name: string;
  domain: string;
  description: string;
  status: string;
  version_count?: number;
  created_at?: string;
  latest_version?: string;
  latest_status?: string;
  case_count?: number;
  updated_at?: string;
}

export interface EvaluationV2DatasetVersion {
  dataset_version_id: string;
  dataset_id: string;
  domain?: string;
  version: string;
  status: string;
  content_hash: string;
  published_by?: string;
  published_at?: string;
  case_count?: number;
  run_refs?: number;
}

export interface EvaluationV2DatasetVersionDiff {
  dataset_id: string;
  base_version: string;
  target_version: string;
  base_content_hash: string;
  target_content_hash: string;
  added: Array<{ case_id: string; revision: number }>;
  removed: Array<{ case_id: string; revision: number }>;
  changed: Array<{
    case_id: string;
    base_revision: number;
    base_hash: string;
    target_revision: number;
    target_hash: string;
  }>;
  unchanged_count: number;
  summary: { added: number; removed: number; changed: number; unchanged: number };
  stale: { base: string[]; target: string[] };
}

export interface EvaluationV2DatasetCoverage {
  dataset_id: string;
  dataset_version_id: string;
  version: string;
  content_hash: string;
  case_count: number;
  dimensions: Record<'category' | 'scenario' | 'risk_level' | 'difficulty', {
    covered_count: number;
    coverage_ratio: number;
    items: Array<{ label: string; count: number; ratio: number }>;
    other_count: number;
  }>;
}

export interface EvaluationV2Candidate {
  candidate_id: string;
  payload_json: {
    input?: { query?: string };
    proposed_facts?: Array<Record<string, unknown>>;
    proposed_evidence?: Array<Record<string, unknown>>;
    source_ref?: Record<string, unknown>;
    [key: string]: unknown;
  };
  review_status: string;
  revision: number;
  promoted_case_id?: string | null;
  promoted_case_revision?: number | null;
  created_by: string;
  created_at: string;
}

export interface EvaluationV2CaseRevision {
  case_id: string;
  lifecycle_status: 'approved' | 'published';
  revision: number;
  input_json: { query: string };
  expected_json: Record<string, unknown>;
  evidence_json: Array<Record<string, unknown>>;
  metadata_json: Record<string, unknown>;
  source_ref: Record<string, unknown>;
  content_hash: string;
}

export interface EvaluationV2ImportManifest {
  schema_version: string;
  dataset_key: string;
  name: string;
  version: string;
  module: string;
  description?: string;
}

export interface EvaluationV2ImportPreview {
  valid: boolean;
  case_count: number;
  errors: Array<{ line: number; field: string; message: string }>;
  preview: Array<{ id: string; candidate: Record<string, unknown> }>;
  persisted: false;
}

export interface EvaluationV2EvidenceClaim {
  kb_id: string;
  doc_id: string;
  doc_version: string;
  quote: string;
}

export interface EvaluationV2EvaluatorVersion {
  evaluator_version_id: string;
  evaluator_key: string;
  version: string;
  evaluator_kind: 'deterministic' | 'evidence' | 'llm_judge' | 'human';
  config_json: Record<string, unknown>;
  rubric_hash: string;
  status: 'draft' | 'published';
  created_by?: string;
  published_by?: string;
}

export interface EvaluationSuite {
  name: string;
  module: string;
  version: string;
  suite_id?: string;
  suite_key?: string;
  suite_version_id?: string;
  suite_hash?: string;
  dataset_version: string;
  kb_id: string;
  fixture_set: string;
  case_count: number;
  case_ids: string[];
  trigger_classes: string[];
  thresholds: Record<string, number>;
  prompt_mapping: Record<string, unknown>;
  tool_mapping: Record<string, unknown>;
  source?: 'evaluation_v2' | 'legacy_unverified';
  read_only?: boolean;
  status?: string;
}

export interface EvaluationV2Run {
  run_id: string;
  suite_version_id: string;
  module?: string;
  suite_key?: string;
  suite_name?: string;
  suite_version?: string;
  job_status: 'queued' | 'preparing' | 'running' | 'aggregating' | 'completed' | 'failed' | 'cancelled' | 'blocked';
  gate_verdict?: 'PASS' | 'FAIL' | 'INVALID' | null;
  gate_reason?: Array<Record<string, unknown>>;
  cost_amount?: string | number | null;
  cost_currency?: string | null;
  total_cases: number;
  completed_cases: number;
  passed_cases: number;
  created_by?: string;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  kb_id?: string;
  corpus_hash?: string;
  env_hash?: string;
  invalid_reason?: string;
  evaluation_mode?: string | null;
  source?: string | null;
  prompt_versions?: Record<string, number | string | null> | null;
}

export interface EvaluationV2RunFingerprint {
  source?: string;
  evaluation_mode?: string;
  runner?: string;
  module?: string;
  dataset_version_id?: string;
  prompt_versions?: Record<string, number | string | null>;
  evaluator_versions?: Array<Record<string, unknown>>;
  generation_model?: string;
  embedding_model?: string;
  rerank_model?: string;
  corpus_snapshot_id?: string;
  dataset_hash?: string;
  corpus_hash?: string;
}

export interface EvaluationV2RunResult {
  result_id: string;
  case_id: string;
  case_revision: number;
  status: 'pass' | 'fail' | 'error' | 'skip';
  scores: Record<string, number | null>;
  failure_stage?: string;
  latency_ms?: number | null;
  cost_amount?: string | number | null;
  cost_currency?: string | null;
}

export interface EvaluationV2HumanRubric {
  evaluator_version_id: string;
  evaluator_key: string;
  version: string;
  rubric_hash: string;
  config: { criteria: Array<{ key: string; label: string; weight: number }> };
}

export interface EvaluationV2HumanReview {
  review_id: string;
  run_id: string;
  case_id: string;
  evaluator_version_id: string;
  request_id?: string;
  label: 'correct' | 'partially_correct' | 'incorrect' | 'unsafe' | 'unverifiable';
  overall_score: number | string;
  scores_json: Record<string, number>;
  rationale: string;
  reviewed_by: string;
  reviewed_at: string;
}

export interface EvaluationV2HumanReviewCollection {
  evaluators: EvaluationV2HumanRubric[];
  reviews: EvaluationV2HumanReview[];
}

export interface EvaluationV2RunCaseDetail {
  run_id: string;
  case_id: string;
  case_revision: number;
  result: {
    status: string;
    expected: Record<string, unknown>;
    actual: Record<string, unknown>;
    metrics: Record<string, number | null>;
    duration_ms: number;
    error_msg?: string | null;
    error_stage?: string | null;
  };
  trace_id?: string | null;
  latency_ms?: number | null;
  cost_amount?: string | number | null;
  cost_currency?: string | null;
  cost_status: string;
}

export interface EvaluationV2Comparison {
  comparable: boolean;
  reasons: string[];
  baseline_run_id: string;
  candidate_run_id: string;
  metrics: Array<{ key: string; baseline: number; candidate: number; delta: number; sample_count: number }>;
  pass_rate_inference: null | {
    sample_count: number;
    excluded_count: number;
    discordant_count: number;
    candidate_only_pass: number;
    baseline_only_pass: number;
    pass_rate_delta: number;
    ci95_lower: number | null;
    ci95_upper: number | null;
    p_value: number;
    p_value_method: 'exact_mcnemar_two_sided';
    confidence_interval_method: 'paired_wilson_95';
  };
  cases: Array<{ case_id: string; question?: string; baseline: string; candidate: string; scores: { baseline: Record<string, number | null>; candidate: Record<string, number | null> } }>;
  slices: Array<{
    field: 'category' | 'scenario' | 'risk_level' | 'difficulty';
    value: string;
    sample_count: number;
    baseline_pass_rate: number;
    candidate_pass_rate: number;
    pass_rate_delta: number;
    pass_rate_inference?: {
      eligible: boolean;
      sample_count: number;
      minimum_sample_count: number;
      reason?: string;
      discordant_count?: number;
      pass_rate_delta?: number;
      ci95_lower?: number | null;
      ci95_upper?: number | null;
      p_value?: number;
      adjusted_p_value?: number;
      correction_method?: 'holm_bonferroni';
      family_size?: number;
    };
    metrics: Array<{ key: string; baseline: number; candidate: number; delta: number; sample_count: number }>;
  }>;
  slices_truncated: number;
  slice_warning?: string;
}

export interface EvaluationV2Experiment {
  experiment_id: string;
  suite_version_id: string;
  name: string;
  baseline_run_id?: string | null;
  candidate_run_id?: string | null;
  created_by?: string;
  created_at?: string;
}

export interface EvaluationV2GateDecision {
  decision_id: string;
  run_id: string;
  policy_hash: string;
  verdict: 'PASS' | 'FAIL' | 'INVALID';
  reasons_json: Array<Record<string, unknown>>;
  decided_by: string;
  decided_at: string;
}

export interface EvaluationV2GateApproval {
  approval_id: string;
  run_id: string;
  gate_decision_id: string;
  request_id?: string;
  decision: 'approved' | 'rejected';
  release_reference: string;
  rationale: string;
  decided_by: string;
  decided_at: string;
}

export interface EvaluationV2Gate {
  run_id: string;
  job_status: string;
  verdict?: string | null;
  reasons: Array<Record<string, unknown>>;
  decisions: EvaluationV2GateDecision[];
  approvals: EvaluationV2GateApproval[];
}

export type EvalResultDetail = EvalRunDetail["report"]["results"][number]

export interface EvalRunDetail {
  run_id: string;
  report: {
    timestamp: string;
    module: string;
    mode: string;
    smoke: boolean;
    tier: string;
    summaries: Array<{
      module: string;
      total: number;
      passed: number;
      failed: number;
      errors: number;
      skipped: number;
      pass_rate: number;
      metrics: Record<string, number>;
    }>;
    results: Array<{
      case_id: string;
      module: string;
      status: "pass" | "fail" | "error" | "skip";
      expected: Record<string, unknown>;
      actual: Record<string, unknown>;
      metrics: Record<string, number>;
      duration_ms: number;
      error_msg: string | null;
      /** C8-4/EVD-08：失败阶段（retrieval/generation/judge/ragas/timeout/…） */
      error_stage?: string | null;
    }>;
    tier_summaries: Array<{
      tier: string;
      total: number;
      passed: number;
      failed: number;
      pass_rate: number;
      threshold: number;
      passed_threshold: boolean;
      /** GATE-12：最低有效样本量门（2026-10-04 一期新增） */
      valid_samples?: number;
      min_samples?: number | null;
      passed_min_samples?: boolean;
      gate_reasons?: string[];
    }>;
    metadata?: Record<string, unknown>;
  };
  meta: Record<string, unknown>;
  /** RUN-01/07/08：run 生命周期状态（旧 run 无状态文件时为 null） */
  run_status?: {
    status: string;
    started_at?: string;
    finished_at?: string;
    error?: string;
    stale?: boolean;
    age_seconds?: number | null;
  } | null;
}

export const evaluationService = {
  listDatasets(module?: string): Promise<DatasetCatalogItem[]> {
    const query = module ? `?module=${encodeURIComponent(module)}` : "";
    return request<{ items: DatasetCatalogItem[] }>(`${BASE}/datasets${query}`).then(result => result.items);
  },

  getDatasetVersion(datasetId: string, version: string): Promise<DatasetVersionDetail> {
    return request<DatasetVersionDetail>(
      `${BASE}/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(version)}`,
    );
  },

  listDatasetCandidates(status?: string): Promise<DatasetCandidate[]> {
    const query = status ? `?status=${encodeURIComponent(status)}` : "";
    return request<{ items: DatasetCandidate[] }>(`${BASE}/dataset-candidates${query}`).then(result => result.items);
  },

  approveDatasetCandidate(candidateId: string, reviewer: string): Promise<DatasetCandidate> {
    return request<DatasetCandidate>(`${BASE}/dataset-candidates/${encodeURIComponent(candidateId)}/approve`, {
      method: "POST",
      body: JSON.stringify({ reviewer }),
    });
  },

  rejectDatasetCandidate(candidateId: string, reviewer: string, reason: string): Promise<DatasetCandidate> {
    return request<DatasetCandidate>(`${BASE}/dataset-candidates/${encodeURIComponent(candidateId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ reviewer, reason }),
    });
  },

  listSuites(): Promise<EvaluationSuite[]> {
    return request<{ items: EvaluationSuite[] }>(`${BASE}/v2/suites`).then(result => result.items);
  },

  listLegacySuites(): Promise<EvaluationSuite[]> {
    return request<{ items: EvaluationSuite[] }>(`${BASE}/suites`).then(result => result.items);
  },

  listV2Datasets(limit: number = 100): Promise<EvaluationV2Dataset[]> {
    return request<{ items: EvaluationV2Dataset[] }>(`${BASE}/v2/datasets?limit=${limit}`).then(result => result.items);
  },

  createV2Dataset(payload: { dataset_key: string; name: string; domain: string; description: string }): Promise<EvaluationV2Dataset> {
    return request<EvaluationV2Dataset>(`${BASE}/v2/datasets`, { method: 'POST', body: payload });
  },

  listV2DatasetVersions(datasetId: string): Promise<EvaluationV2DatasetVersion[]> {
    return request<{ items: EvaluationV2DatasetVersion[] }>(`${BASE}/v2/datasets/${encodeURIComponent(datasetId)}/versions`).then(result => result.items);
  },

  compareV2DatasetVersions(datasetId: string, baseVersionId: string, targetVersionId: string): Promise<EvaluationV2DatasetVersionDiff> {
    const query = new URLSearchParams({ base_version_id: baseVersionId, target_version_id: targetVersionId });
    return request<EvaluationV2DatasetVersionDiff>(`${BASE}/v2/datasets/${encodeURIComponent(datasetId)}/versions/compare?${query.toString()}`);
  },

  async exportV2DatasetVersion(datasetId: string, datasetVersionId: string): Promise<Blob> {
    const response = await fetchRaw(
      `${BASE}/v2/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(datasetVersionId)}/export`,
    );
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw apiErrorFromEnvelope(payload, response.status);
    }
    return response.blob();
  },

  getV2DatasetCoverage(datasetId: string, datasetVersionId: string): Promise<EvaluationV2DatasetCoverage> {
    return request<EvaluationV2DatasetCoverage>(
      `${BASE}/v2/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(datasetVersionId)}/coverage`,
    );
  },

  listV2Evaluators(): Promise<EvaluationV2EvaluatorVersion[]> {
    return request<{ items: EvaluationV2EvaluatorVersion[] }>(`${BASE}/v2/evaluators`).then(result => result.items);
  },

  createV2Evaluator(payload: { evaluator_key: string; version: string; evaluator_kind: EvaluationV2EvaluatorVersion['evaluator_kind']; config: Record<string, unknown> }): Promise<EvaluationV2EvaluatorVersion> {
    return request<EvaluationV2EvaluatorVersion>(`${BASE}/v2/evaluators`, { method: 'POST', body: payload });
  },

  publishV2Evaluator(evaluatorVersionId: string): Promise<EvaluationV2EvaluatorVersion> {
    return request<EvaluationV2EvaluatorVersion>(`${BASE}/v2/evaluator-versions/${encodeURIComponent(evaluatorVersionId)}/publish`, { method: 'POST' });
  },

  createV2Suite(payload: {
    suite_key: string;
    name: string;
    module: string;
    dataset_version_id: string;
    version: string;
    case_selection: Record<string, unknown>;
    evaluator_versions: Array<{ evaluator_version_id: string; binding: string }>;
    metric_policy: Record<string, unknown>;
    security_policy: Record<string, unknown>;
    minimum_valid_samples: number;
  }): Promise<{ suite_id: string; suite_version_id: string; version: string; status: string; snapshot_hash: string }> {
    return request(`${BASE}/v2/suites`, { method: 'POST', body: payload });
  },

  publishV2Suite(suiteVersionId: string): Promise<{ suite_version_id: string; status: string; snapshot_hash: string }> {
    return request(`${BASE}/v2/suite-versions/${encodeURIComponent(suiteVersionId)}/publish`, { method: 'POST' });
  },

  listV2Runs(limit: number = 100): Promise<EvaluationV2Run[]> {
    return request<EvaluationV2Run[]>(`${BASE}/v2/runs?limit=${limit}`);
  },

  listV2Experiments(limit: number = 100): Promise<EvaluationV2Experiment[]> {
    return request<EvaluationV2Experiment[]>(`${BASE}/v2/experiments?limit=${limit}`);
  },

  createV2Experiment(payload: { suite_version_id: string; name: string }): Promise<EvaluationV2Experiment> {
    return request<EvaluationV2Experiment>(`${BASE}/v2/experiments`, { method: 'POST', body: payload });
  },

  attachV2ExperimentRun(experimentId: string, payload: { run_id: string; slot: 'baseline' | 'candidate' }): Promise<EvaluationV2Experiment> {
    return request<EvaluationV2Experiment>(`${BASE}/v2/experiments/${encodeURIComponent(experimentId)}/runs`, { method: 'POST', body: payload });
  },

  getV2Run(runId: string): Promise<{ run: EvaluationV2Run; fingerprint: EvaluationV2RunFingerprint }> {
    return request<{ run: EvaluationV2Run; fingerprint: EvaluationV2RunFingerprint }>(`${BASE}/v2/runs/${encodeURIComponent(runId)}`);
  },

  async downloadImportTemplate(module: string, format: 'jsonl' | 'csv' | 'fields'): Promise<void> {
    const response = await fetchRaw(`${BASE}/v2/import-template?module=${encodeURIComponent(module)}&format=${encodeURIComponent(format)}`);
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="([^"]+)"/);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = match?.[1] || `eval-template.${format}`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  },

  searchDatasetEvidence(payload: { snapshot_id: string; query: string; top_k?: number }): Promise<{
    snapshot_id: string;
    kb_id: string;
    items: Array<{ doc_id: string; chunk_id: string; chunk_index: number; title: string; snippet: string; content: string; doc_version?: string; score: number }>;
  }> {
    return request(`${BASE}/v2/datasets/evidence-search`, { method: 'POST', body: payload });
  },

  createV2Run(payload: { suite_version_id: string; source?: string; evaluation_mode?: string; kb_id?: string; request_id: string; subject_user_id?: number; prompt_versions?: Record<string, number | string | null> }): Promise<{ run: EvaluationV2Run; task: Record<string, unknown>; reused: boolean }> {
    return request(`${BASE}/v2/runs`, { method: 'POST', body: payload });
  },

  cancelV2Run(runId: string): Promise<{ run_id: string; accepted: boolean; job_status: string; task?: Record<string, unknown> }> {
    return request(`${BASE}/v2/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' });
  },

  retryV2Run(runId: string): Promise<{ run_id: string; task: Record<string, unknown> }> {
    return request(`${BASE}/v2/runs/${encodeURIComponent(runId)}/retry`, { method: 'POST' });
  },

  listV2RunResults(runId: string, limit: number = 200): Promise<EvaluationV2RunResult[]> {
    return request<EvaluationV2RunResult[]>(`${BASE}/v2/runs/${encodeURIComponent(runId)}/results?limit=${limit}`);
  },

  getV2HumanReviews(runId: string, caseId: string): Promise<EvaluationV2HumanReviewCollection> {
    return request<EvaluationV2HumanReviewCollection>(`${BASE}/v2/runs/${encodeURIComponent(runId)}/results/${encodeURIComponent(caseId)}/human-reviews`);
  },

  createV2HumanReview(runId: string, caseId: string, payload: {
    evaluator_version_id: string;
    request_id: string;
    label: EvaluationV2HumanReview['label'];
    scores: Record<string, number>;
    rationale: string;
  }): Promise<EvaluationV2HumanReview> {
    return request<EvaluationV2HumanReview>(`${BASE}/v2/runs/${encodeURIComponent(runId)}/results/${encodeURIComponent(caseId)}/human-reviews`, { method: 'POST', body: payload });
  },

  getV2RunCaseDetail(runId: string, caseId: string): Promise<EvaluationV2RunCaseDetail> {
    return request<EvaluationV2RunCaseDetail>(`${BASE}/v2/runs/${encodeURIComponent(runId)}/results/${encodeURIComponent(caseId)}`);
  },

  compareV2Runs(baselineRunId: string, candidateRunId: string): Promise<EvaluationV2Comparison> {
    const query = new URLSearchParams({ baseline_run_id: baselineRunId, candidate_run_id: candidateRunId });
    return request<EvaluationV2Comparison>(`${BASE}/v2/experiments/compare?${query.toString()}`);
  },

  getV2Gate(runId: string): Promise<EvaluationV2Gate> {
    return request(`${BASE}/v2/gates/${encodeURIComponent(runId)}`);
  },

  createV2GateApproval(runId: string, payload: {
    gate_decision_id: string;
    request_id: string;
    decision: 'approved' | 'rejected';
    release_reference: string;
    rationale: string;
  }): Promise<EvaluationV2GateApproval> {
    return request(`${BASE}/v2/gates/${encodeURIComponent(runId)}/approvals`, { method: 'POST', body: payload });
  },

  listV2AuditEvents(params: { target_type?: string; target_id?: string; limit?: number } = {}): Promise<Array<Record<string, unknown>>> {
    const query = new URLSearchParams();
    if (params.target_type) query.set('target_type', params.target_type);
    if (params.target_id) query.set('target_id', params.target_id);
    query.set('limit', String(params.limit || 100));
    return request(`${BASE}/v2/audit-events?${query.toString()}`);
  },

  publishV2DatasetVersion(datasetId: string, payload: { version: string; items: Array<{ case_id: string; revision: number }> }): Promise<EvaluationV2DatasetVersion> {
    return request<EvaluationV2DatasetVersion>(`${BASE}/v2/datasets/${encodeURIComponent(datasetId)}/versions`, { method: 'POST', body: payload });
  },

  listV2Candidates(reviewStatus?: string, limit: number = 100): Promise<EvaluationV2Candidate[]> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (reviewStatus) params.set('review_status', reviewStatus);
    return request<{ items: EvaluationV2Candidate[] }>(`${BASE}/v2/candidates?${params.toString()}`).then(result => result.items);
  },

  previewV2Import(file: File, manifest: EvaluationV2ImportManifest): Promise<EvaluationV2ImportPreview> {
    const body = new FormData();
    body.append('file', file);
    body.append('manifest', JSON.stringify(manifest));
    return request<EvaluationV2ImportPreview>(`${BASE}/v2/datasets/imports/preview`, { method: 'POST', body });
  },

  importV2Candidates(file: File, manifest: EvaluationV2ImportManifest, requestId: string): Promise<{ request_id: string; candidate_count: number; candidate_ids: string[]; review_status: string }> {
    const body = new FormData();
    body.append('file', file);
    body.append('manifest', JSON.stringify(manifest));
    body.append('request_id', requestId);
    return request(`${BASE}/v2/datasets/imports`, { method: 'POST', body });
  },

  createV2Candidate(payload: { source_kind: 'manual'; payload: { input: { query: string }; proposed_facts: Array<{ behavior: string; reference_answer: string; required_facts: string[] }>; proposed_evidence: Array<Record<string, unknown>>; metadata: Record<string, unknown> }; idempotency_key: string }): Promise<EvaluationV2Candidate> {
    return request(`${BASE}/v2/candidates`, { method: 'POST', body: payload });
  },

  reviewV2Candidate(candidateId: string, payload: {
    expected_revision: number;
    verdict: 'approved' | 'rejected' | 'needs_changes';
    rationale: string;
    expected?: Record<string, unknown>;
    evidence?: EvaluationV2EvidenceClaim[];
  }): Promise<EvaluationV2Candidate> {
    return request<EvaluationV2Candidate>(`${BASE}/v2/candidates/${encodeURIComponent(candidateId)}/review`, { method: 'POST', body: payload });
  },

  getV2CaseLatestRevision(caseId: string): Promise<EvaluationV2CaseRevision> {
    return request<EvaluationV2CaseRevision>(`${BASE}/v2/cases/${encodeURIComponent(caseId)}/latest-revision`);
  },

  createV2CaseRevisionCandidate(caseId: string, payload: {
    query: string;
    observed_answer: string;
    idempotency_key: string;
  }): Promise<EvaluationV2Candidate> {
    return request<EvaluationV2Candidate>(`${BASE}/v2/cases/${encodeURIComponent(caseId)}/revision-candidates`, { method: 'POST', body: payload });
  },

  intakeV2TraceCandidate(traceId: string, idempotencyKey: string): Promise<EvaluationV2Candidate> {
    return request<EvaluationV2Candidate>(`${BASE}/v2/intake/from-trace`, {
      method: 'POST', body: { trace_id: traceId, idempotency_key: idempotencyKey },
    });
  },

  createFromTrace(payload: FromTracePayload): Promise<AppendResult> {
    return request<AppendResult>(BASE + "/cases/from-trace", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  listCases(module: string): Promise<EvalCase[]> {
    return request<EvalCase[]>(`${BASE}/cases?module=${encodeURIComponent(module)}`);
  },

  runEval(module: string = "rag", options?: { evaluation_mode?: EvaluationMode; suite?: string }): Promise<RunEvalResult> {
    return request<RunEvalResult>(`${BASE}/run?module=${encodeURIComponent(module)}`, {
      method: "POST",
      body: JSON.stringify(options || { evaluation_mode: 'offline', suite: 'pr_baseline' }),
      timeout: 120000,
    });
  },

  listRuns(limit: number = 20): Promise<RunSummary[]> {
    return request<RunSummary[]>(`${BASE}/runs?limit=${limit}`);
  },

  getRun(runId: string): Promise<EvalRunDetail> {
    return request<EvalRunDetail>(`${BASE}/runs/${encodeURIComponent(runId)}`);
  },

  /** C2-1/RUN-03：协作式取消（幂等；已完成样本保留，剩余记 skip） */
  cancelRun(runId: string): Promise<{ run_id: string; cancel_requested: boolean; run_status: string; already_cancelled?: boolean; audit_recorded?: boolean }> {
    return request<{ run_id: string; cancel_requested: boolean; run_status: string; already_cancelled?: boolean; audit_recorded?: boolean }>(
      `${BASE}/runs/${encodeURIComponent(runId)}/cancel`,
      { method: 'POST' },
    );
  },

  /** C2-6/REL-09：run 操作审计（取消/重跑/熔断留痕） */
  listRunOperations(runId: string): Promise<{ run_id: string; operations: Array<{ operation: string; actor: string; detail: string; created_at: string }> }> {
    return request<{ run_id: string; operations: Array<{ operation: string; actor: string; detail: string; created_at: string }> }>(
      `${BASE}/runs/${encodeURIComponent(runId)}/operations`,
    );
  },
};
