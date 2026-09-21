"use client";

import { Span, TraceRecord, formatCost } from "@/types/trace";

/**
 * 成本侧栏：本次 trace 的总成本 + 按 span 拆解
 */

interface Props {
  trace: TraceRecord;
}

export default function CostPanel({ trace }: Props) {
  const totalUsd = trace.cost_usd ?? 0;
  const spans = trace.spans || [];
  const totalTokens = trace.usage?.total_tokens ?? 0;

  // P0-2: 成本数据是否采集到（有 cost / 有 token / 有 per-span llm 成本任一即算）
  const collected = totalUsd > 0 || totalTokens > 0
    || spans.some((s) => Number(s.llm_call?.cost_usd ?? 0) > 0
      || Number(s.metrics?.cost_usd ?? 0) > 0);

  // 按 span 聚合成本（llm_call 字段优先，回退 span metrics — 回填数据在 metrics 里）
  const perStep = spans
    .map((s) => ({
      label: s.name,
      ms: s.duration_ms,
      usd: Number(s.llm_call?.cost_usd ?? s.metrics?.cost_usd ?? 0),
    }))
    .filter((s) => s.usd > 0)
    .sort((a, b) => b.usd - a.usd);

  const maxUsd = Math.max(...perStep.map((x) => x.usd), 0.000001);

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
      <div>
        <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">总成本</p>
        {collected ? (
          <>
            <p className="font-mono text-xl font-bold text-emerald-600">{formatCost(totalUsd)}</p>
            <p className="text-[10px] text-slate-400 mt-0.5">后端价格表权威值</p>
          </>
        ) : (
          <p className="text-xs text-slate-400 mt-1" title="llm_usage 明细缺失，无法计算本次调用成本">未采集</p>
        )}
      </div>

      <div>
        <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-2">分步骤成本</p>
        {perStep.length === 0 ? (
          <p className="text-xs text-slate-400">{collected ? "无 LLM 调用成本" : "Token 用量未采集"}</p>
        ) : (
          <div className="space-y-1.5">
            {perStep.map((s, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className="w-20 text-slate-600 truncate">{s.label}</span>
                <div className="flex-1 h-4 bg-slate-100 rounded overflow-hidden">
                  <div
                    className="h-full bg-emerald-400"
                    style={{ width: `${(s.usd / maxUsd) * 100}%` }}
                  />
                </div>
                <span className="font-mono text-slate-600 tabular-nums w-20 text-right">{formatCost(s.usd)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="pt-3 border-t border-slate-100 space-y-1 text-[10px] text-slate-400">
        <div className="flex justify-between"><span>模型</span><span className="font-mono text-slate-600">{trace.model.name}</span></div>
        <div className="flex justify-between"><span>Provider</span><span className="font-mono text-slate-600">{trace.model.provider}</span></div>
        <div className="flex justify-between"><span>总 Token</span><span className="font-mono text-slate-600">{totalTokens > 0 ? totalTokens : "未采集"}</span></div>
        <div className="flex justify-between"><span>单 Token</span><span className="font-mono text-slate-600">
          {totalTokens ? formatCost(totalUsd / totalTokens) : "--"}
        </span></div>
      </div>
    </div>
  );
}
