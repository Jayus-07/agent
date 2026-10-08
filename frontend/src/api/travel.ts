/**
 * src/api/travel.ts — 旅游域接口（用户端，2026-10-01 收口）
 *
 * 为什么要有这个模块：`/travel` 页原本把所有请求内联在 page.tsx 里
 * （`request('/api/travel/plan')` 等），页面上再挂一个「对话改行程」抽屉后，
 * **两处**都要调同一个 `/api/travel/plan` —— 会话 id、超时、body 形状三样
 * 任何一处写歪，两处行为就会不一致（典型症状：抽屉里改完，页面没更新）。
 * 按 src/api/surface.test.ts 的「每域一个模块」约定落到本文件，页面与抽屉
 * 都从这里取，形状只有一份。
 *
 * 契约要点（与 backend/app/api/routes/travel.py 对齐）：
 *   - `/plan` 是**非流式**：域图是确定性规则规划（天气/路况/POI 校验），
 *     秒级返回完整 itinerary JSON —— 结构化行程（坐标/时刻/费用）走 SSE
 *     文本流会丢，地图打点与 ICS 导出都做不了，所以这里不用 SSE。
 *   - **跨轮改单靠 conversation_id**：同一个值第二次进来，后端
 *     （travel/graph_builder 的 PostgresSaver checkpointer）会带上上一轮
 *     brief 做合并 + 指纹比对，需求变了才重排 —— 这就是「对话改行程」
 *     能生效的机制。thread 状态活 7 天（TRAVEL_CHECKPOINT_TTL_DAYS）。
 *     注意：checkpointer 一旦降级成内存版（多 worker 不共享、重启即失），
 *     TRAVEL_REQUIRE_PERSISTENCE 开着时后端会**拒绝复用**跨轮产物并如实告知
 *     「跨轮修改行程暂不可用」—— 此时抽屉不会改坏行程，只会出一份新行程。
 *   - ✅ 时下实机状态：app 日志 `[TravelGraph] checkpointer enabled
 *     (PostgresSaver: postgres/agent_memory)`，跨轮改单可用。
 */
import { apiErrorFromEnvelope, fetchRaw, request } from "./client";
import { parseSSEStream } from "@/lib/sse-parser";

// ── 类型（后端 travel 契约的最小投影） ────────────────────────

export interface ItineraryPoi {
  poi_id: string;
  name: string;
  lat: number;
  lng: number;
  ticket_cny: number;
  source?: string;
  verification_status?: string;
  /** 坐标级核实状态（字段级拆分）：verified=坐标来自实时检索源；缺省回退 source 口径 */
  location_status?: string;
  /** 入选理由（2026-10-03）：「为什么选它」——检索来源/必去点名/知乎攻略提及 */
  reason?: string;
  required?: boolean;
}

export interface ItineraryItem {
  title: string;
  kind: string;
  start: string;
  end: string;
  minutes: number;
  wait_minutes: number;
  note: string;
  poi: ItineraryPoi | null;
}

/** 公交/地铁候选（验收 #41）：候选参考，与主路线口径不同源，不作时间轴计算 */
export interface TransitOption {
  /** 公交全程分钟数（含候车） */
  duration_min: number;
  /** 公交路程（米） */
  distance_m: number;
  /** 乘坐摘要，如「步行至东街口 → 乘坐地铁1号线」 */
  summary: string;
  is_estimate: boolean;
}

export interface TransitLeg {
  from_title: string;
  to_title: string;
  minutes: number;
  distance_km: number;
  mode: string;
  cost_cny: number;
  source: string;
  observed_at?: string | null;
  traffic_aware?: boolean;
  is_estimate?: boolean;
  fallback_reason?: string | null;
  /** 公交/地铁候选（#41）；缺省=无候选（接口失败/远期日期），不显示 */
  transit_option?: TransitOption | null;
}

export interface ItineraryDay {
  day_index: number;
  day_date: string | null;
  items: ItineraryItem[];
  legs?: TransitLeg[];
  active_minutes: number;
  transit_minutes: number;
  cost_cny: number;
}

/** 需求摘要（后端 travel/models/brief.py TravelBrief 的投影，按实测响应核对） */
export interface ItineraryBrief {
  destination: string;
  origin: string;
  start_date: string | null;
  days: number | null;
  party_size: number;
  budget_cny: number | null;
  /** 预算语义：hard=不得超出，soft=大概范围。 */
  budget_constraint?: 'hard' | 'soft' | string;
  weather_conditions?: Array<{ day_index: number; condition: string; action: string }>;
  preferences: string[];
  must_go: string[];
  avoid: string[];
  /** 节奏档位 relaxed / moderate / intense */
  pace: string;
  /** 方案档位（M3-e）：economy | comfortable */
  tier?: string;
  /** 饮食忌口（如「不吃辣」） */
  diet: string;
  lodging: string;
  transport: string;
}

/** 表单直接传递给旅游域的事实字段，空值允许缺省而不是改写成自然语言。 */
export interface TravelBriefInput {
  destination?: string;
  origin?: string;
  start_date?: string | null;
  days?: number | null;
  party_size?: number;
  budget_cny?: number | null;
  preferences?: string[];
  must_go?: string[];
  avoid?: string[];
  pace?: string;
  diet?: string;
  lodging?: string;
  transport?: string;
}

export type TravelRequestMode = 'plan' | 'chat' | 'action';

export interface TravelUiContext {
  selected_day?: number;
  selected_poi_id?: string;
}

/** 与后端 TravelPlanRequest 对齐的 JSON 请求体（snake_case wire format）。 */
export interface TravelPlanRequestPayload {
  message: string;
  session_id: string;
  conversation_id: string;
  client_run_id?: string;
  source?: TravelSource;
  mode?: TravelRequestMode;
  brief_input?: TravelBriefInput;
  base_plan_version?: number | null;
  ui_context?: TravelUiContext;
  action_payload?: Record<string, unknown>;
}

export interface TravelResponseMetadata {
  result_kind: 'answer' | 'plan' | 'draft' | 'clarification' | 'task_result';
  conversation_id: string;
  turn_id: string;
  active_plan_version: number | null;
  draft_plan_version: number | null;
  base_plan_version: number | null;
  task_results: Array<Record<string, unknown>>;
}

export interface ItineraryCost {
  tickets: number;
  meals: number;
  lodging: number;
  transit: number;
  /** 后端 pydantic @property —— model_dump() 不含该字段，前端需自兜底求和 */
  total?: number;
}

export interface IntercityTrain {
  train_no: string;
  start_time: string;
  arrive_time: string;
  duration: string;
  /** 席别→余票（中文席别键）；可能延迟，非官方聚合源 */
  seats: Record<string, string | number>;
  /** 席别→票价（元）；仅实时并查过的车次（前 2 个）有值，其余为空对象 */
  prices: Record<string, string | number>;
  from_station: string;
  to_station: string;
  date: string;
  source: string;
  queried_at: string;
}

export interface Itinerary {
  brief: ItineraryBrief;
  days: ItineraryDay[];
  cost: ItineraryCost;
  status: string;
  plan_version: number;
  warnings: string[];
  sources?: string[];
  confidence?: number;
  data_snapshot_version?: string;
  changed_fields?: string[];
  change_reason?: string;
  /** 城际班次摘要（12306 实时检索；空=未触发车票查询） */
  intercity?: IntercityTrain[];
}

export interface TravelClarificationOption {
  label: string;
  days: number | null;
  message: string;
}

/**
 * 消息来源归因（M4/G3）：随消息体传 source，后端写进 trace.tags。
 * manual=手打输入；card_action=结果卡按钮；canvas_action=画布换一家/就近唤醒；
 * tier_switch=档位切换；budget_negotiate=缺口协商。
 */
export type TravelSource =
  | 'manual'
  | 'card_action'
  | 'canvas_action'
  | 'tier_switch'
  | 'budget_negotiate';

/** 用户决策留痕上报体（M4/G1）；user_id/时间戳由服务端从身份与数据库取 */
export interface TravelDecisionInput {
  decision:
    | 'apply_draft'
    | 'discard_draft'
    | 'canvas_replace'
    | 'tier_switch'
    | 'budget_negotiate';
  conversationId: string;
  planVersion?: number;
  tierFrom?: string;
  tierTo?: string;
  payload?: Record<string, unknown>;
  source?: string;
  clientRunId?: string;
}

/** 结构化「为什么这样排」（后端 reporter._build_rationale，M2 验收反馈拍板形态） */
export interface RationaleData {
  headline?: { days?: number; spots?: number; must_go?: string[] }
  tradeoffs?: {
    dropped?: Array<{ name: string; reason: string }>
    kept_required?: string[]
    unscheduled?: string[]
    unscheduled_extra?: number
  }
  verified?: string[]
  pace_rule?: string
  version_note?: string
  _has_decision_required?: boolean
  /** M3-f 预算协商（自动降档 / 缺口卡数据） */
  budget_negotiation?: {
    tier_downgraded?: boolean
    economy_total_cny?: number
    floor_total_cny?: number
    gap_cny?: number
    note?: string
  }
}

export interface PlanResponse {
  /** 结构化规划说明；旧数据/降级无此字段 → 前端回退 Markdown */
  rationale?: RationaleData;
  /** success | answered | needs_clarification | needs_user_decision | failed */
  status: string;
  final_answer: string;
  itinerary: Itinerary | null;
  clarification?: string;
  clarification_options?: TravelClarificationOption[];
  intent?: string;
  plan_status?: 'waiting_confirmation' | 'confirmed' | string;
  change_record?: Record<string, unknown> | null;
  result_kind?: TravelResponseMetadata['result_kind'];
  conversation_id?: string;
  turn_id?: string;
  active_plan_version?: number | null;
  draft_plan_version?: number | null;
  base_plan_version?: number | null;
  task_results?: Array<Record<string, unknown>>;
}

export interface CityGuide {
  destination: string;
  source_level: 1 | 2 | 3 | 4;
  status: 'ok' | 'empty';
  note: string;
  summary: { title: string; doc_id: string; summary: string } | null;
  tips: string[];
  guides: Array<Record<string, unknown>>;
}

/** 城市指南（M3-g）：缓存优先，四级内容链 */
export async function fetchCityGuide(destination: string, opts?: { force?: boolean; signal?: AbortSignal }): Promise<CityGuide> {
  const params = new URLSearchParams({ destination });
  if (opts?.force) params.set('force', 'true');
  return request(`/api/travel/city-guide?${params.toString()}`, { signal: opts?.signal }) as Promise<CityGuide>;
}

export interface TravelPlanVersion {
  conversation_id: string;
  plan_version: number;
  plan_status: string;
  destination: string;
  change: Record<string, unknown>;
  created_at: string;
}

/** 历史规划列表项：每会话最新版元数据（不含 itinerary 正文）。 */
export interface TravelPlanSummary {
  conversation_id: string;
  plan_version: number;
  plan_status: string;
  destination: string;
  created_at: string;
  versions_count: number;
}

/** 历史规划最新版详情：点击列表项恢复时取的完整行程。 */
export interface TravelPlanLatest {
  conversation_id: string;
  plan_version: number;
  plan_status: string;
  destination: string;
  created_at: string;
  itinerary: Itinerary | null;
  /** 服务端最新行为 draft 时，单独返回当前 Active 内容供刷新恢复。 */
  active_plan_version?: number | null;
  active_plan_status?: string | null;
  active_itinerary?: Itinerary | null;
}

export interface TravelPlanDiff {
  from_version: number;
  to_version: number;
  added: string[];
  removed: string[];
  moved: Array<{ poi_id: string; from_day: number; to_day: number }>;
  brief_fields: string[];
}

export interface Recommendation {
  city: string;
  score: number;
  highlights: string[];
  matched_preferences: string[];
}

/**
 * 规划超时 55s（< 网关 60s 读超时）：让前端先拿到干净的超时提示，而不是等 504。
 */
export const TRAVEL_PLAN_TIMEOUT_MS = 55_000;

export interface TravelPlanOptions {
  signal?: AbortSignal;
  clientRunId?: string;
  source?: TravelSource;
  mode?: TravelRequestMode;
  briefInput?: TravelBriefInput;
  basePlanVersion?: number | null;
  uiContext?: TravelUiContext;
  actionPayload?: Record<string, unknown>;
}

function buildTravelPlanRequestPayload(
  message: string,
  conversationId: string,
  options: TravelPlanOptions,
): TravelPlanRequestPayload {
  return {
    message,
    session_id: conversationId,
    conversation_id: conversationId,
    mode: options.mode ?? 'plan',
    ...(options.clientRunId ? { client_run_id: options.clientRunId } : {}),
    ...(options.source ? { source: options.source } : {}),
    ...(options.briefInput ? { brief_input: options.briefInput } : {}),
    ...(options.basePlanVersion !== undefined
      ? { base_plan_version: options.basePlanVersion } : {}),
    ...(options.uiContext ? { ui_context: options.uiContext } : {}),
    ...(options.actionPayload ? { action_payload: options.actionPayload } : {}),
  };
}

export type TravelStreamEventName =
  | "run.started"
  | "requirement.interpreted"
  | "stage.started"
  | "stage.finished"
  | "tool.started"
  | "tool.result"
  | "run.finished"
  | "done"
  | "error"
  | "ping"

export interface TravelStreamEvent {
  event: TravelStreamEventName
  data: Record<string, unknown>
}

// ── 端点 ─────────────────────────────────────────────────────

/**
 * 旅游规划 / 跨轮改单。
 *
 * `conversationId` 既是会话线程也是 checkpoint 的 thread_id —— 同一行程上
 * 「继续改」必须复用同一个值，换新行程才轮换（见 components/travel/planState）。
 */
export function planTravel(
  message: string,
  conversationId: string,
  options: TravelPlanOptions = {},
): Promise<PlanResponse> {
  return request<PlanResponse>("/api/travel/plan", {
    method: "POST",
    // body 直传对象：client.ts 的 JSON 层会序列化并补 Content-Type
    // （2026-09-22 复盘：直传对象出网成 "[object Object]" 的根因已在该层收口）
    body: buildTravelPlanRequestPayload(message, conversationId, options),
    timeout: TRAVEL_PLAN_TIMEOUT_MS,
    signal: options.signal,
  });
}

/**
 * 旅游规划真实事件流。事件只来自旅游域图与实际 Tool/Provider 调用；
 * `done.data.result` 是与非流式 `/plan` 相同的 PlanResponse。
 */
export async function* streamTravelPlan(
  message: string,
  conversationId: string,
  options: TravelPlanOptions = {},
): AsyncGenerator<TravelStreamEvent> {
  const response = await fetchRaw("/api/travel/plan/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildTravelPlanRequestPayload(message, conversationId, options)),
    signal: options.signal,
  })

  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => ({}))
    throw apiErrorFromEnvelope(payload, response.status)
  }

  for await (const event of parseSSEStream(response.body, options.signal)) {
    yield event as TravelStreamEvent
  }
}

/** 目的地推荐（无偏好时是纯热度榜，仍有参考价值）。 */
export function fetchTravelRecommendations(
  preferences: string[] = [],
  top = 3,
): Promise<Recommendation[]> {
  const qs = new URLSearchParams({ top: String(top) });
  if (preferences.length) qs.set("preferences", preferences.join(","));
  return request<{ recommendations: Recommendation[] }>(
    `/api/travel/recommend?${qs.toString()}`,
  ).then((r) => r.recommendations ?? []);
}

/** 行程 → ICS 日历文件（服务端纯转换；这里只取 Blob，下载动作留给页面）。 */
export async function fetchItineraryIcs(itinerary: Itinerary): Promise<Blob> {
  const res = await fetchRaw("/api/travel/export/ics", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ itinerary }),
  });
  if (!res.ok) throw new Error(`导出失败（HTTP ${res.status}）`);
  return res.blob();
}

/** 行程反馈（复用既有 feedback 表）。 */
export function sendTravelFeedback(input: {
  conversationId: string;
  vote: "positive" | "negative";
  reason?: string;
  destination?: string;
  planVersion?: number;
}): Promise<unknown> {
  return request("/api/travel/feedback", {
    method: "POST",
    body: {
      session_id: input.conversationId,
      vote: input.vote,
      reason: input.reason ?? "",
      destination: input.destination ?? "",
      plan_version: input.planVersion ?? 0,
    },
  });
}

/**
 * 用户决策留痕（M4/G1）：草案应用/放弃、画布确认替换、档位切换、
 * 删减协商逐条上报。**软失败语义**——留痕是审计旁路不是业务门禁，
 * 网络失败/后端不可用时静默吞掉（返回 null），调用方照常继续本地更新。
 * 后端同样按软失败设计（写失败返回 recorded=false 而非 5xx）。
 */
export async function recordTravelDecision(input: TravelDecisionInput): Promise<unknown> {
  try {
    return await request("/api/travel/decisions", {
      method: "POST",
      body: {
        decision: input.decision,
        conversation_id: input.conversationId,
        plan_version: input.planVersion ?? 0,
        tier_from: input.tierFrom ?? "",
        tier_to: input.tierTo ?? "",
        payload: input.payload ?? {},
        source: input.source ?? "",
        client_run_id: input.clientRunId ?? "",
      },
    });
  } catch {
    // 留痕失败不挡 UI 动作（审计旁路口径，与后端软失败对称）
    return null;
  }
}

/** 版本历史：返回元数据，不拉取旧行程正文。 */
export function fetchTravelPlanVersions(conversationId: string): Promise<{
  conversation_id: string;
  versions: TravelPlanVersion[];
}> {
  return request(`/api/travel/plans/${encodeURIComponent(conversationId)}/versions`)
}

/** 历史规划列表：当前用户名下每个会话的最新版，按最近更新新→旧。 */
export function fetchTravelPlanList(limit = 30): Promise<TravelPlanSummary[]> {
  return request<{ plans: TravelPlanSummary[] }>(`/api/travel/plans?limit=${limit}`)
    .then((r) => r.plans ?? [])
}

/** ── 分类候选表（验收 #10）────────────────────────────────── */

export interface TravelCandidate {
  poi_id: string;
  name: string;
  category: string;
  rating: number;
  /** 入选理由（检索来源/知乎提及等），可空 */
  reason: string;
  source: string;
}

export interface TravelCandidatesResponse {
  conversation_id: string;
  /** 候选池所属的行程版本（候选随重排失效，旧版本要可见地标注，#102 口径） */
  plan_version: number;
  destination: string | null;
  /** 按大类分组：景点/美食/酒店；空组=无候选数据（前端隐藏 tab，如交通/酒店） */
  groups: Record<string, TravelCandidate[]>;
  /** false=候选池不可达（checkpoint 过期/降级），列表为空且 hint 说明原因 */
  available: boolean;
  hint: string;
}

export function fetchTravelCandidates(conversationId: string): Promise<TravelCandidatesResponse> {
  return request(
    `/api/travel/candidates?conversation_id=${encodeURIComponent(conversationId)}`,
  )
}

/** 某个历史规划的最新版（含完整 itinerary），供点击历史项恢复。 */
export function fetchTravelPlanLatest(conversationId: string): Promise<TravelPlanLatest> {
  return request(`/api/travel/plans/${encodeURIComponent(conversationId)}/latest`)
}

/** 两个版本之间的确定性差异。 */
export function fetchTravelPlanDiff(
  conversationId: string,
  fromVersion: number,
  toVersion: number,
): Promise<TravelPlanDiff> {
  const qs = new URLSearchParams({
    from_version: String(fromVersion),
    to_version: String(toVersion),
  })
  return request(`/api/travel/plans/${encodeURIComponent(conversationId)}/diff?${qs}`)
}

/** 确认当前行程版本，和“应用修改”语义分开。 */
export function confirmTravelPlan(
  conversationId: string,
  planVersion: number,
): Promise<{ status: string; plan_version: number; plan_status: string }> {
  return request('/api/travel/plans/confirm', {
    method: 'POST',
    body: { conversation_id: conversationId, plan_version: planVersion },
  })
}

/** 恢复历史版本：以旧内容生成新版本，使用 base_version 做 CAS。 */
export function restoreTravelPlan(
  conversationId: string,
  targetVersion: number,
  baseVersion: number,
): Promise<{ status: string; itinerary: Itinerary; plan_status: string }> {
  return request('/api/travel/plans/restore', {
    method: 'POST',
    body: {
      conversation_id: conversationId,
      target_version: targetVersion,
      base_version: baseVersion,
    },
  })
}

/** 浏览器授权定位后只取城市级结果，不把精确坐标写入旅游会话。 */
export async function reverseGeocodeTravelOrigin(
  lat: number,
  lng: number,
): Promise<{ city: string; label: string } | null> {
  const qs = new URLSearchParams({ location: `${lat},${lng}` })
  const result = await request<{
    found?: boolean;
    city?: string;
    district?: string;
    result?: { city?: string; district?: string } | null;
  }>(`/api/map/reverse-geocode?${qs}`)
  if (!result.found) return null
  const city = result.city || result.result?.city || ''
  if (!city) return null
  const district = result.district || result.result?.district || ''
  return { city, label: district ? `${city} · ${district}` : city }
}

/** 用户旅游偏好（travel_preferences 表投影；后端 GET/PUT /api/travel/preferences） */
export interface TravelPrefs {
  origin: string;
  preferences: string[];
  pace: '' | 'relaxed' | 'moderate' | 'intense';
  diet: string;
  lodging: string;
  transport: string;
}

const EMPTY_PREFS: TravelPrefs = {
  origin: '', preferences: [], pace: '', diet: '', lodging: '', transport: '',
};

export function emptyTravelPrefs(): TravelPrefs {
  return { ...EMPTY_PREFS, preferences: [] };
}

/** 是否从未填过偏好（引导问卷触发条件：全空 = 新用户） */
export function isEmptyPrefs(p: TravelPrefs): boolean {
  return (
    !p.origin && p.preferences.length === 0 && !p.pace &&
    !p.diet && !p.lodging && !p.transport
  );
}

/** GET /api/travel/preferences — 读当前用户偏好（404/空 = 新用户） */
export async function fetchMyPreferences(): Promise<TravelPrefs> {
  try {
    const data = await request<Partial<TravelPrefs>>('/api/travel/preferences');
    return {
      origin: data.origin ?? '',
      preferences: Array.isArray(data.preferences) ? data.preferences : [],
      pace: (data.pace ?? '') as TravelPrefs['pace'],
      diet: data.diet ?? '',
      lodging: data.lodging ?? '',
      transport: data.transport ?? '',
    };
  } catch {
    // 401/404/网络失败都按「无偏好」处理——引导问卷是增强，不是前置门
    return emptyTravelPrefs();
  }
}

/** PUT /api/travel/preferences — 写入问卷收集的偏好 */
export async function saveMyPreferences(prefs: TravelPrefs): Promise<boolean> {
  const res = await fetchRaw('/api/travel/preferences', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(prefs),
  });
  return res.ok;
}
