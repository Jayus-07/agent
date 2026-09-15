"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { RefreshCw, Coins, ArrowDownToLine, ArrowUpFromLine, Wallet, Hash, Database, BrainCircuit, ScrollText, ChevronDown } from "lucide-react";

// recharts 较重，拆为独立 chunk 懒加载，避免路由切换时阻塞渲染
const TokenCharts = dynamic(() => import("./TokenCharts"), {
  ssr: false,
  loading: () => <div className="h-64 rounded-lg bg-slate-100 animate-pulse" />,
});
import { useToast } from "@/components/shared/Toast";
import {
  getTokensSummary,
  getTokensCalls,
  type TokensSummary,
  type TokenUsageDaily,
  type TokenCallRow,
} from "@/api/observability";

// 时间窗选项（天）
const RANGE_OPTIONS = [
  { days: 1, label: "今天" },
  { days: 7, label: "近 7 天" },
  { days: 30, label: "近 30 天" },
  { days: 90, label: "近 90 天" },
];

// 组件类型选项
const COMPONENT_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "llm", label: "LLM" },
  { value: "embedding", label: "Embedding" },
  { value: "rerank", label: "Reranker" },
  { value: "ocr", label: "OCR" },
];

type MetricMode = "tokens" | "cost";
type CurrencyMode = "usd" | "cny";

const USD_CNY = 7.25;

function formatNum(n: number | undefined | null): string {
  return (n ?? 0).toLocaleString("zh-CN");
}

function formatCost(n: number | undefined | null, currency: CurrencyMode = "usd"): string {
  const usd = n ?? 0;
  if (currency === "cny") {
    const v = usd * USD_CNY;
    if (v > 0 && v < 0.01) return `¥${v.toFixed(4)}`;
    return `¥${v.toFixed(2)}`;
  }
  if (usd > 0 && usd < 0.01) return `$${usd.toFixed(6)}`;
  return `$${usd.toFixed(4)}`;
}

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

const CALL_PAGE_SIZE = 20;

export default function TokensPage() {
  const toast = useToast();
  const [data, setData] = useState<TokensSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(7);
  const [component, setComponent] = useState("all");
  const [metric, setMetric] = useState<MetricMode>("tokens");
  const [currency, setCurrency] = useState<CurrencyMode>("usd");

  // ── 调用明细 ─
  const [calls, setCalls] = useState<TokenCallRow[]>([]);
  const [callsTotal, setCallsTotal] = useState(0);
  const [callsPage, setCallsPage] = useState(1);
  const [callsModel, setCallsModel] = useState<string>("");
  const [callsLoading, setCallsLoading] = useState(false);
  const [showCalls, setShowCalls] = useState(false);

  const load = useCallback(async (d: number, comp: string) => {
    setLoading(true);
    try {
      setData(await getTokensSummary(d, comp));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "加载 Token 用量失败");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(days, component); }, [load, days, component]);

  const loadCalls = useCallback(async (d: number, page: number, model: string, comp: string) => {
    setCallsLoading(true);
    try {
      const r = await getTokensCalls(d, {
        model: model || undefined,
        component: comp || undefined,
        limit: CALL_PAGE_SIZE,
        offset: (page - 1) * CALL_PAGE_SIZE,
      });
      setCalls(r.calls);
      setCallsTotal(r.total);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "加载调用明细失败");
    } finally {
      setCallsLoading(false);
    }
  }, [toast]);

  // 明细面板首次展开 / 筛选或翻页变化时加载
  useEffect(() => {
    if (showCalls) loadCalls(days, callsPage, callsModel, component);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showCalls, days, callsPage, callsModel, component]);

  const totals = data?.totals;
  const daily: TokenUsageDaily[] = data?.daily ?? [];
  const models = data?.models ?? [];

  // 趋势图数据：day → "9/10"
  const chartData = daily.map((d) => ({
    ...d,
    label: d.day.slice(5).replace("-", "/"),
  }));

  const hasCached = (totals?.cached_tokens ?? 0) > 0;
  const hasReasoning = (totals?.reasoning_tokens ?? 0) > 0;

  const cards = [
    { icon: <Coins size={16} />, label: "总 Token", value: formatNum(totals?.total_tokens), sub: `${formatNum(totals?.calls)} 次 LLM 调用` },
    { icon: <ArrowDownToLine size={16} />, label: "输入 Token", value: formatNum(totals?.prompt_tokens), sub: hasCached ? `含缓存命中 ${formatNum(totals?.cached_tokens)}` : "prompt" },
    { icon: <ArrowUpFromLine size={16} />, label: "输出 Token", value: formatNum(totals?.completion_tokens), sub: hasReasoning ? `含推理 ${formatNum(totals?.reasoning_tokens)}` : "completion" },
    { icon: <Wallet size={16} />, label: "预估成本", value: formatCost(totals?.cost_usd, currency), sub: currency === "cny" ? "按模型单价估算 (¥)" : "按模型单价估算" },
    { icon: <Hash size={16} />, label: "请求轮次", value: formatNum(totals?.requests), sub: "按 trace 去重" },
  ];

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* 头部 */}
        <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">Token 用量</h1>
            <p className="text-xs text-text-muted mt-0.5">LLM / Embedding / Reranker / OCR 消耗与成本统计</p>
          </div>
          <div className="flex items-center gap-2">
            {/* 组件类型筛选 */}
            <div className="flex rounded-lg border border-slate-200 bg-white overflow-hidden">
              {COMPONENT_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => { setComponent(opt.value); setCallsPage(1); }}
                  className={`px-3 py-1.5 text-xs transition-colors ${
                    component === opt.value
                      ? "bg-accent text-white"
                      : "text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            {/* 时间范围筛选 */}
            <div className="flex rounded-lg border border-slate-200 bg-white overflow-hidden">
              {RANGE_OPTIONS.map((opt) => (
                <button
                  key={opt.days}
                  onClick={() => { setDays(opt.days); setCallsPage(1); }}
                  className={`px-3 py-1.5 text-xs transition-colors ${
                    days === opt.days
                      ? "bg-accent text-white"
                      : "text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            {/* 货币切换 */}
            <div className="flex rounded-lg border border-slate-200 bg-white overflow-hidden">
              <button
                onClick={() => setCurrency("usd")}
                className={`px-2.5 py-1.5 text-xs transition-colors ${
                  currency === "usd" ? "bg-accent text-white" : "text-slate-600 hover:bg-slate-50"
                }`}
              >
                $ USD
              </button>
              <button
                onClick={() => setCurrency("cny")}
                className={`px-2.5 py-1.5 text-xs transition-colors ${
                  currency === "cny" ? "bg-accent text-white" : "text-slate-600 hover:bg-slate-50"
                }`}
              >
                ¥ CNY
              </button>
            </div>
            <button
              onClick={() => load(days, component)}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 disabled:opacity-50"
            >
              <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
              刷新
            </button>
          </div>
        </div>

        {/* 汇总卡片 */}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 mb-6">
          {cards.map((c) => (
            <div key={c.label} className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
              <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-widest text-slate-400">
                {c.icon}
                {c.label}
              </div>
              <div className="mt-2 text-xl font-semibold font-mono tabular-nums text-slate-800">
                {loading ? "—" : c.value}
              </div>
              <div className="mt-1 text-[11px] text-slate-400">{c.sub}</div>
            </div>
          ))}
        </div>

        {/* 趋势图（成本 / Token 切换） */}
        <div className="bg-white border border-slate-200 rounded-xl p-4 mb-6 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <div className="text-xs font-medium text-slate-500">每日消耗趋势</div>
            <div className="flex rounded-lg border border-slate-200 overflow-hidden">
              <button
                onClick={() => setMetric("tokens")}
                className={`px-3 py-1 text-xs transition-colors ${
                  metric === "tokens" ? "bg-accent text-white" : "text-slate-600 hover:bg-slate-50"
                }`}
              >
                Token 数
              </button>
              <button
                onClick={() => setMetric("cost")}
                className={`px-3 py-1 text-xs transition-colors ${
                  metric === "cost" ? "bg-accent text-white" : "text-slate-600 hover:bg-slate-50"
                }`}
              >
                成本
              </button>
            </div>
          </div>
          {chartData.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-xs text-slate-400">
              暂无数据 — 发起对话后这里会展示每日趋势
            </div>
          ) : (
            <TokenCharts data={chartData} metric={metric} currency={currency} />
          )}
        </div>

        {/* 按模型细分 */}
        <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-1.5 text-xs font-medium text-slate-500">
            <Database size={13} />
            按模型细分
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-widest text-slate-400 border-b border-slate-100">
                  <th className="px-4 py-2.5 font-medium">Provider / Model</th>
                  <th className="px-4 py-2.5 font-medium text-right">调用次数</th>
                  <th className="px-4 py-2.5 font-medium text-right">输入 Token</th>
                  <th className="px-4 py-2.5 font-medium text-right">输出 Token</th>
                  <th className="px-4 py-2.5 font-medium text-right">总 Token</th>
                  {hasCached && <th className="px-4 py-2.5 font-medium text-right">缓存命中</th>}
                  {hasReasoning && <th className="px-4 py-2.5 font-medium text-right">推理</th>}
                  <th className="px-4 py-2.5 font-medium text-right">预估成本</th>
                </tr>
              </thead>
              <tbody>
                {models.length === 0 ? (
                  <tr>
                    <td colSpan={hasCached && hasReasoning ? 8 : 6} className="px-4 py-10 text-center text-slate-400">
                      {loading ? "加载中…" : "暂无数据"}
                    </td>
                  </tr>
                ) : (
                  models.map((m, i) => (
                    <tr key={`${m.provider}-${m.model}-${i}`} className="border-b border-slate-50 hover:bg-slate-50/60">
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-1.5 font-medium text-slate-700">
                          <BrainCircuit size={13} className="text-slate-400" />
                          {m.model || <span className="text-slate-400">未知模型</span>}
                        </div>
                        <div className="text-[10px] text-slate-400 mt-0.5">{m.provider || "-"}</div>
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono tabular-nums">{formatNum(m.calls)}</td>
                      <td className="px-4 py-2.5 text-right font-mono tabular-nums">{formatNum(m.prompt_tokens)}</td>
                      <td className="px-4 py-2.5 text-right font-mono tabular-nums">{formatNum(m.completion_tokens)}</td>
                      <td className="px-4 py-2.5 text-right font-mono tabular-nums font-medium">{formatNum(m.total_tokens)}</td>
                      {hasCached && <td className="px-4 py-2.5 text-right font-mono tabular-nums text-emerald-600">{formatNum(m.cached_tokens)}</td>}
                      {hasReasoning && <td className="px-4 py-2.5 text-right font-mono tabular-nums text-violet-600">{formatNum(m.reasoning_tokens)}</td>}
                      <td className="px-4 py-2.5 text-right font-mono tabular-nums">{formatCost(m.cost_usd, currency)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* 调用明细（可折叠） */}
        <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden mt-6">
          <button
            onClick={() => setShowCalls((v) => !v)}
            className="w-full px-4 py-3 border-b border-slate-100 flex items-center justify-between text-xs font-medium text-slate-500 hover:bg-slate-50/60"
          >
            <span className="flex items-center gap-1.5">
              <ScrollText size={13} />
              调用明细
              {callsTotal > 0 && (
                <span className="text-[10px] font-normal text-slate-400">（共 {formatNum(callsTotal)} 条）</span>
              )}
            </span>
            <ChevronDown size={14} className={`transition-transform ${showCalls ? "rotate-180" : ""}`} />
          </button>

          {showCalls && (
            <>
              <div className="px-4 py-2.5 border-b border-slate-100 flex items-center gap-2">
                <select
                  value={callsModel}
                  onChange={(e) => { setCallsModel(e.target.value); setCallsPage(1); }}
                  className="text-xs border border-slate-200 rounded-lg px-2 py-1 bg-white text-slate-600"
                >
                  <option value="">全部模型</option>
                  {models.map((m, i) => (
                    <option key={`${m.provider}-${m.model}-${i}`} value={m.model}>
                      {m.model || "未知模型"}
                    </option>
                  ))}
                </select>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-[10px] uppercase tracking-widest text-slate-400 border-b border-slate-100">
                      <th className="px-4 py-2.5 font-medium">时间</th>
                      <th className="px-4 py-2.5 font-medium">类型</th>
                      <th className="px-4 py-2.5 font-medium">模型</th>
                      <th className="px-4 py-2.5 font-medium text-right">输入</th>
                      <th className="px-4 py-2.5 font-medium text-right">输出</th>
                      <th className="px-4 py-2.5 font-medium text-right">总 Token</th>
                      {hasCached && <th className="px-4 py-2.5 font-medium text-right">缓存</th>}
                      {hasReasoning && <th className="px-4 py-2.5 font-medium text-right">推理</th>}
                      <th className="px-4 py-2.5 font-medium text-right">成本</th>
                      <th className="px-4 py-2.5 font-medium text-right">耗时</th>
                      <th className="px-4 py-2.5 font-medium">Trace</th>
                    </tr>
                  </thead>
                  <tbody>
                    {calls.length === 0 ? (
                      <tr>
                        <td colSpan={11} className="px-4 py-8 text-center text-slate-400">
                          {callsLoading ? "加载中…" : "该时间窗内暂无调用记录"}
                        </td>
                      </tr>
                    ) : (
                      calls.map((c, i) => (
                        <tr key={`${c.ts}-${i}`} className="border-b border-slate-50 hover:bg-slate-50/60">
                          <td className="px-4 py-2 whitespace-nowrap text-slate-500 tabular-nums">
                            {(() => {
                              // ISO8601 UTC → 本地时间 (MM-DD HH:mm:ss)
                              const d = new Date(c.ts);
                              const mm = String(d.getMonth() + 1).padStart(2, "0");
                              const dd = String(d.getDate()).padStart(2, "0");
                              const hh = String(d.getHours()).padStart(2, "0");
                              const mi = String(d.getMinutes()).padStart(2, "0");
                              const ss = String(d.getSeconds()).padStart(2, "0");
                              return `${mm}-${dd} ${hh}:${mi}:${ss}`;
                            })()}
                          </td>
                          <td className="px-4 py-2">
                            <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${
                              c.component === "llm" ? "bg-blue-100 text-blue-700" :
                              c.component === "embedding" ? "bg-emerald-100 text-emerald-700" :
                              c.component === "ocr" ? "bg-amber-100 text-amber-700" :
                              "bg-violet-100 text-violet-700"
                            }`}>
                              {c.component === "llm" ? "LLM" :
                               c.component === "embedding" ? "Emb" :
                               c.component === "ocr" ? "OCR" : "Rnk"}
                            </span>
                          </td>
                          <td className="px-4 py-2">
                            <div className="font-medium text-slate-700">{c.model || "未知模型"}</div>
                            <div className="text-[10px] text-slate-400">{c.provider || "-"}</div>
                          </td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums">{formatNum(c.prompt_tokens)}</td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums">{formatNum(c.completion_tokens)}</td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums font-medium">{formatNum(c.total_tokens)}</td>
                          {hasCached && <td className="px-4 py-2 text-right font-mono tabular-nums text-emerald-600">{formatNum(c.cached_tokens)}</td>}
                          {hasReasoning && <td className="px-4 py-2 text-right font-mono tabular-nums text-violet-600">{formatNum(c.reasoning_tokens)}</td>}
                          <td className="px-4 py-2 text-right font-mono tabular-nums">{formatCost(c.cost_usd, currency)}</td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums text-slate-500">
                            {c.duration_ms ? `${(c.duration_ms / 1000).toFixed(1)}s` : "-"}
                          </td>
                          <td className="px-4 py-2">
                            {c.trace_id ? (
                              <a
                                href={`/observability/traces/${c.trace_id}`}
                                className="font-mono text-accent hover:underline"
                                title={c.trace_id}
                              >
                                {c.trace_id.slice(0, 8)}
                              </a>
                            ) : (
                              <span className="text-slate-300">-</span>
                            )}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
              {/* 分页 */}
              {callsTotal > CALL_PAGE_SIZE && (
                <div className="px-4 py-2.5 flex items-center justify-between text-xs text-slate-500">
                  <span>
                    第 {(callsPage - 1) * CALL_PAGE_SIZE + 1}-{Math.min(callsPage * CALL_PAGE_SIZE, callsTotal)} 条 / 共 {formatNum(callsTotal)} 条
                  </span>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setCallsPage((pg) => Math.max(1, pg - 1))}
                      disabled={callsPage <= 1 || callsLoading}
                      className="px-3 py-1 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-40"
                    >
                      上一页
                    </button>
                    <button
                      onClick={() => setCallsPage((pg) => (pg * CALL_PAGE_SIZE < callsTotal ? pg + 1 : pg))}
                      disabled={callsPage * CALL_PAGE_SIZE >= callsTotal || callsLoading}
                      className="px-3 py-1 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-40"
                    >
                      下一页
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        <p className="mt-4 text-[11px] text-slate-400">
          成本按 <code className="font-mono">AVAILABLE_MODELS</code> 单价表估算{currency === "cny" ? `（汇率 $1 = ¥${USD_CNY}）` : ""}，未登记单价的模型计为 {currency === "cny" ? "¥0" : "$0"}；
          缓存命中 / 推理 Token 取决于上游 API 是否返回对应明细。
        </p>
      </div>
    </div>
  );
}
