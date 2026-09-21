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
  };
  meta: Record<string, unknown>;
}

export const evaluationService = {
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
