/**
 * C1-4/REG-08：发布审批对比（candidate vs production）。
 * 数据源 GET /prompts/{key}/releases/{release_id}/comparison（后端聚合
 * 候选指标 / production 最近 run / 基线文件三路信息）。
 */
import { request } from '@/api/client'

const BASE = '/api/prompts'

export interface ReleaseComparison {
  release_id: string;
  prompt_key: string;
  candidate_version: number;
  production_version: number | null;
  candidate: {
    eval_run_id: string;
    metrics: Record<string, number>;
    ragas: Record<string, number>;
    gate?: {
      tier_pass: boolean | null;
      sample_pass: boolean | null;
      regression_pass: boolean | null;
      ragas_pass: boolean | null;
      blocked_rules: Array<{ rule: string; expected: string; actual: string; severity: string; message?: string }>;
    };
  };
  production_runs: Array<{
    run_id: string;
    pass_rate: number | null;
    case_count: number;
    pass_count: number;
    metrics: Record<string, number>;
    created_at: string;
  }>;
  baseline: { available: boolean; run_id?: string; dataset_version?: string; note?: string };
  deltas: {
    available: boolean;
    note?: string;
    regressions?: Record<string, { baseline: number; current: number; delta: number }>;
  };
}

export function getReleaseComparison(key: string, releaseId: string): Promise<ReleaseComparison> {
  return request<ReleaseComparison>(
    `${BASE}/${encodeURIComponent(key)}/releases/${encodeURIComponent(releaseId)}/comparison`,
  )
}
