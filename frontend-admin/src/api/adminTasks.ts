/**
 * 管理端任务中心 API（/api/admin/tasks/*）
 *
 * 后端：backend/app/api/routes/admin_tasks.py（管理员闸 require_admin_operator）。
 * 所有操作（retry/revoke）后端要求 body.confirm=true 二次确认 + 60s 冷却。
 * 后端 403 时抛 ApiError，由页面显示「需要管理员」而不是静默空列表。
 */
import { request } from "@/api/client";

/** 任务状态（与后端 TaskStatus 枚举对齐；Celery STARTED→RUNNING / RETRY→FAILED+retry>0 / REVOKED→CANCELLED） */
export const TASK_STATUSES = [
  "PENDING",
  "RUNNING",
  "WAITING_USER",
  "PAUSED",
  "SUCCESS",
  "FAILED",
  "CANCELLED",
] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];

/** 任务行（列表用；重字段 traceback/input 只在详情接口返回） */
export interface TaskRow {
  task_id: string;
  status: TaskStatus | string;
  progress: string;
  current_node: string;
  result: unknown;
  error_message: string;
  error_type: string;
  retry_count: number;
  max_retries: number;
  duration_ms: number | null;
  queue: string;
  worker: string;
  trace_id: string;
  biz_type: string;
  biz_id: string;
  graph_name: string;
  tenant_id: string;
  created_at: string | null;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string | null;
}

export interface TaskListParams {
  status?: string;
  graph_name?: string;
  queue?: string;
  worker?: string;
  user_id?: string;
  biz_type?: string;
  biz_id?: string;
  trace_id?: string;
  retries_gt?: number;
  hours?: number;
  limit?: number;
  offset?: number;
}

export interface TaskListResponse {
  tasks: TaskRow[];
  total: number;
  limit: number;
  offset: number;
}

/** 统计聚合（GET /admin/tasks/stats） */
export interface TaskStats {
  window_hours: number;
  total: number;
  by_status: Record<string, { count: number; p95_duration_ms: number | null; avg_duration_ms: number | null }>;
  success_rate: number | null;
  failure_rate: number | null;
  p95_duration_ms: number | null;
  retried_count: number;
  recent_failures?: {
    task_id: string;
    error_type: string;
    error_message: string;
    worker: string;
    retry_count: number;
    finished_at: string | null;
  }[];
}

/** 详情（GET /admin/tasks/{id}）：额外暴露 traceback / input / 内部定位字段 */
export interface TaskDetail extends TaskRow {
  traceback: string;
  celery_task_id: string;
  thread_id: string;
  input: Record<string, unknown>;
  parent_task_id: string;
}

/** 节点 checkpoint 历史（GET /admin/tasks/{id}/checkpoints） */
export interface TaskCheckpoint {
  id: number;
  node_name: string;
  state_json: Record<string, unknown>;
  created_at: string;
}

function qs(params: TaskListParams): string {
  const pairs: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    pairs.push(`${k}=${encodeURIComponent(String(v))}`);
  }
  return pairs.length ? `?${pairs.join("&")}` : "";
}

/** GET /admin/tasks — 全局任务列表 */
export async function listAdminTasks(params: TaskListParams = {}): Promise<TaskListResponse> {
  return await request<TaskListResponse>(`/api/admin/tasks${qs(params)}`);
}

/** GET /admin/tasks/stats — 统计聚合 */
export async function getAdminTaskStats(hours = 24): Promise<TaskStats> {
  return await request<TaskStats>(`/api/admin/tasks/stats?hours=${hours}`);
}

/** GET /admin/tasks/{id} — 详情（含 traceback） */
export async function getAdminTask(taskId: string): Promise<TaskDetail> {
  return await request<TaskDetail>(`/api/admin/tasks/${taskId}`);
}

/** GET /admin/tasks/{id}/checkpoints — 节点历史 */
export async function getAdminTaskCheckpoints(taskId: string): Promise<TaskCheckpoint[]> {
  const data = await request<{ checkpoints: TaskCheckpoint[] }>(
    `/api/admin/tasks/${taskId}/checkpoints`);
  return data.checkpoints;
}

/** POST /admin/tasks/{id}/retry — 重试（confirm 必传 true；60s 冷却 429 会抛 ApiError） */
export async function retryAdminTask(taskId: string, reason = ""): Promise<{ message: string }> {
  return await request<{ message: string }>(`/api/admin/tasks/${taskId}/retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: true, reason }),
  });
}

/** POST /admin/tasks/{id}/revoke — 撤销（同上） */
export async function revokeAdminTask(taskId: string, reason = ""): Promise<{ message: string }> {
  return await request<{ message: string }>(`/api/admin/tasks/${taskId}/revoke`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: true, reason }),
  });
}
