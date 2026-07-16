/**
 * LLM 业务 API：模型列表 / 切换 / 余额 / MultiQuery 模式
 */
import { request } from "../fetcher";

export interface LLMModel {
  provider: string;
  name: string;
  display: string;
  description: string;
}

export interface LLMBalance {
  ok: boolean;
  provider?: string;
  balance?: string;
  currency?: string;
  note?: string;
  error?: string;
}

/** GET /llm/models — 列出可用模型 */
export async function listLLMModels(): Promise<{ models: LLMModel[]; current: string }> {
  return request("/llm/models");
}

/** GET /llm/current — 获取当前模型 */
export async function getCurrentLLM(): Promise<{ model: string; provider: string }> {
  return request("/llm/current");
}

/** POST /llm/switch — 切换当前模型 */
export async function switchLLM(model: string): Promise<{ ok: boolean; model?: string; provider?: string; error?: string }> {
  return request("/llm/switch", {
    method: "POST",
    body: JSON.stringify({ model }),
  });
}

/** GET /llm/balance — 查询余额 */
export async function getLLMBalance(provider?: string): Promise<LLMBalance> {
  const qs = provider ? `?provider=${encodeURIComponent(provider)}` : "";
  try {
    return await request<LLMBalance>(`/llm/balance${qs}`);
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

/** GET /llm/multiquery — 获取 MultiQuery 模式 */
export async function getMultiQueryMode(): Promise<{ mode: string }> {
  return request("/llm/multiquery");
}

/** POST /llm/multiquery — 设置 MultiQuery 模式 */
export async function setMultiQueryMode(mode: string): Promise<{ ok: boolean; mode: string }> {
  return request("/llm/multiquery", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}