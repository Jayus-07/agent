/**
 * lib/api.ts — 向后兼容层
 * 新代码请直接 import 对应子模块：
 *   import { streamChat } from '@/api/chat'
 *   import { listLLMModels } from '@/api/llm'
 *   import { listSessions } from '@/lib/api/memory'
 *   import { listTraces } from '@/lib/api/observability'
 */

export * from "@/api/chat";
export * from "@/api/llm";
export * from "./api/memory";
export * from "./api/observability";

export { ApiError } from "./fetcher";
export { parseSSEFrame, parseSSEStream } from "./sse-parser";