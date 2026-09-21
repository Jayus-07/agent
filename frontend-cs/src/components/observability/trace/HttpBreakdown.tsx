"use client";

import { Span } from "@/types/trace";

/**
 * HTTP 耗时拆分：DNS / Connect / TLS / TTFB / Body
 */

interface Props {
  step: Span;  // 兼容旧名，接受 Span
}

export default function HttpBreakdown({ step }: Props) {
  const b = step.http_breakdown;
  if (!b) {
    return <p className="text-xs text-slate-400">本步骤无 HTTP 拆分数据</p>;
  }
  const total = b.dns_ms + b.connect_ms + b.tls_ms + b.ttfb_ms + b.body_ms;
  if (total === 0) return null;

  const segments = [
    { key: "DNS", ms: b.dns_ms, color: "bg-blue-300" },
    { key: "Connect", ms: b.connect_ms, color: "bg-cyan-300" },
    { key: "TLS", ms: b.tls_ms, color: "bg-teal-300" },
    { key: "TTFB", ms: b.ttfb_ms, color: "bg-violet-400" },
    { key: "Body", ms: b.body_ms, color: "bg-emerald-400" },
  ];

  return (
    <div className="space-y-2">
      <div className="flex w-full h-3 rounded overflow-hidden bg-slate-100">
        {segments.map((s) => {
          if (s.ms === 0) return null;
          return (
            <div
              key={s.key}
              title={`${s.key}: ${s.ms}ms (${((s.ms / total) * 100).toFixed(1)}%)`}
              className={`${s.color} h-full`}
              style={{ width: `${(s.ms / total) * 100}%` }}
            />
          );
        })}
      </div>
      <div className="grid grid-cols-5 gap-1 text-[10px]">
        {segments.map((s) => (
          <div key={s.key} className="text-center">
            <div className={`w-2 h-2 ${s.color} rounded-full mx-auto mb-0.5`} />
            <div className="text-slate-500">{s.key}</div>
            <div className="font-mono text-slate-700 tabular-nums">{s.ms}ms</div>
          </div>
        ))}
      </div>
    </div>
  );
}
