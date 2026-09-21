import { mutationRequest, request } from "@/api/client";

export type CandidateStatus = "pending" | "approved" | "rejected" | "promoted";
export interface FeedbackCandidate {
  candidate_id: string; status: CandidateStatus; trace_id: string; module: string;
  summary?: string; reason?: string; correction_text?: string; expected_answer?: string;
  created_at: string; reviewed_at?: string; reviewer?: string;
}

export function canPromoteCandidate(status: CandidateStatus): boolean { return status === "approved"; }

export async function listFeedbackCandidates(status?: CandidateStatus): Promise<{ items: FeedbackCandidate[] }> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<{ items: FeedbackCandidate[] }>(`/api/feedback/candidates${query}`);
}
export async function reviewFeedbackCandidate(candidateId: string, decision: "approve" | "reject", note: string): Promise<FeedbackCandidate> {
  const result = await mutationRequest<{ candidate: FeedbackCandidate }>(`/api/feedback/candidates/${encodeURIComponent(candidateId)}/${decision}`, { operation: `feedback-review:${candidateId}:${decision}`, method: "POST", body: { note } });
  return result.candidate;
}
export async function promoteFeedbackCandidate(candidateId: string): Promise<{ status: CandidateStatus; case_id?: string; appended: boolean }> {
  return mutationRequest<{ status: CandidateStatus; case_id?: string; appended: boolean }>(`/api/feedback/candidates/${encodeURIComponent(candidateId)}/promote`, { operation: `feedback-promote:${candidateId}`, method: "POST", body: {} });
}
