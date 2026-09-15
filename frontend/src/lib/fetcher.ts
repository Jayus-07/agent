/**
 * lib/fetcher.ts — 兼容层（P0-1a 起）
 *
 * ⚠️ 实现已迁移至 `@/api/client`。新代码请直接 import：
 *   import { request, requestSilent, ApiError } from '@/api/client'
 *
 * 本文件仅做 re-export，保留一个发布周期后移除。
 * Response 层（`authFetch`）见 `@/lib/authFetch`，迁移由 auth 会话负责。
 */

export {
  ApiError,
  backendBaseUrl,
  fetchRaw,
  request,
  requestSilent,
} from "@/api/client";
export type { BackendId, RequestOptions } from "@/api/client";
