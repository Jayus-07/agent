/**
 * SSE v2 解析器：支持 event: 字段分流
 * 纯函数 + async generator，无业务依赖，可独立测试
 */

export interface SSEEvent<T = unknown> {
  event: string;
  data: T;
}

/**
 * 安全解析单个 SSE 事件帧
 */
export function parseSSEFrame(evtType: string, jsonStr: string): SSEEvent | null {
  if (!evtType || !jsonStr) return null;
  try {
    const data = JSON.parse(jsonStr);
    return { event: evtType, data };
  } catch {
    return null;
  }
}

/**
 * 把一个 ReadableStream 解析为 SSE 事件流
 * @param body fetch 的 res.body
 * @param signal AbortSignal 用于外部取消
 */
export async function* parseSSEStream(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "";

  try {
    while (true) {
      if (signal?.aborted) return;

      const { done, value } = await reader.read();
      if (done) {
        const parsed = parseSSEFrame(currentEvent, buffer.trim());
        if (parsed) yield parsed;
        return;
      }

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed === "") continue; // 空行 = 帧边界
        if (trimmed.startsWith("event: ")) {
          currentEvent = trimmed.slice(7);
          continue;
        }
        if (trimmed.startsWith("data: ")) {
          const jsonStr = trimmed.slice(6);
          const parsed = parseSSEFrame(currentEvent, jsonStr);
          if (parsed) yield parsed;
          currentEvent = "";
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}