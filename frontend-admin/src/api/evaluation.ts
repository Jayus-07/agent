import { request } from "@/lib/fetcher";

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
}

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
}

export interface DatasetCatalogItem {
  dataset_id: string;
  module: string;
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

export interface EvaluationSuite {
  name: string;
  module: string;
  version: string;
  dataset_version: string;
  kb_id: string;
  fixture_set: string;
  case_count: number;
  case_ids: string[];
  trigger_classes: string[];
  thresholds: Record<string, number>;
  prompt_mapping: Record<string, unknown>;
  tool_mapping: Record<string, unknown>;
}

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
    }>;
    tier_summaries: Array<{
      tier: string;
      total: number;
      passed: number;
      failed: number;
      pass_rate: number;
      threshold: number;
      passed_threshold: boolean;
    }>;
    metadata?: Record<string, unknown>;
  };
  meta: Record<string, unknown>;
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
    return request<{ items: EvaluationSuite[] }>(`${BASE}/suites`).then(result => result.items);
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

  runEval(module: string = "rag"): Promise<RunEvalResult> {
    return request<RunEvalResult>(`${BASE}/run?module=${encodeURIComponent(module)}`, {
      method: "POST",
      timeout: 120000,
    });
  },

  listRuns(limit: number = 20): Promise<RunSummary[]> {
    return request<RunSummary[]>(`${BASE}/runs?limit=${limit}`);
  },

  getRun(runId: string): Promise<EvalRunDetail> {
    return request<EvalRunDetail>(`${BASE}/runs/${encodeURIComponent(runId)}`);
  },
};
