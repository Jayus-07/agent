import { request } from "@/api/client";

export type IdempotencyOperationStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "uncertain";

export interface IdempotencyOperationSummary {
  client_key: string;
  operation: string;
  status: IdempotencyOperationStatus | string;
  attempt: number;
  error_code?: string | null;
  has_result: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  expires_at?: string | null;
}

export function getIdempotencyOperationStatus(
  clientKey: string,
): Promise<IdempotencyOperationSummary> {
  return request<IdempotencyOperationSummary>(
    `/api/idempotency/operations/${encodeURIComponent(clientKey)}`,
  );
}
