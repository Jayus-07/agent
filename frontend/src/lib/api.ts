/**
 * lib/api.ts — 向后兼容层
 * 新代码请直接 import 对应子模块：
 *   import { streamChat } from '@/lib/api/chat'
 *   import { listLLMModels } from '@/lib/api/llm'
 *   import { listSessions } from '@/lib/api/memory'
 */

export * from "./api/chat";
export * from "./api/llm";
export * from "./api/memory";

export { ApiError } from "./fetcher";
export { parseSSEFrame, parseSSEStream } from "./sse-parser";