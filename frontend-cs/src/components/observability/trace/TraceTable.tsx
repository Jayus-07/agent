"use client";

import { useRouter } from "next/navigation";
import { TraceRecord, statusDot, durationColor, durationBg } from "@/types/trace";

interface Props {
  traces: TraceRecord[];
}

function truncate(s: string, n: number): string {
  if (!s) return "--";
  return s.length > n ? s.slice(0, n) + "..." : s;
}

function formatTime(iso: string): string {
  if (!iso) return "--";
  const d = new Date(iso);
  return d.toLocaleTimeString("zh-CN", { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
}

export default function TraceTable({ traces }: Props) {
  const router = useRouter();

  if (!traces.length) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-slate-400">
        <p className="text-lg">暂无 Trace 数据</p>
        <p className="text-sm mt-1">等待 Agent 请求产生第一条 Trace</p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">
            <th className="py-3 px-4 w-10">状态</th>
            <th className="py-3 px-4">Trace ID</th>
            <th className="py-3 px-4">用户问题</th>
            <th className="py-3 px-4">模型回答</th>
            <th className="py-3 px-4 w-24 text-right">总耗时</th>
            <th className="py-3 px-4 w-24 text-right">Token</th>
            <th className="py-3 px-4">Session</th>
            <th className="py-3 px-4 w-28">时间</th>
            <th className="py-3 px-4 w-20"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {traces.map((t) => {
            const hasError = t.error && Object.keys(t.error).length > 0;
            const status = hasError ? "error" : "success";
            return (
              <tr
                key={t.id}
                onClick={() => router.push(`/observability/traces/${t.id}`)}
                className={`group cursor-pointer transition-colors hover:bg-slate-50 ${durationBg(t.duration_ms)}`}
              >
                {/* Status dot */}
                <td className="py-3 px-4">
                  <span className={`inline-block w-2.5 h-2.5 rounded-full ${statusDot(status)}`} />
                </td>

                {/* Trace ID */}
                <td className="py-3 px-4">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-slate-600 group-hover:text-slate-900">
                      {t.id.slice(0, 12)}
                    </span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        navigator.clipboard.writeText(t.id);
                      }}
                      className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-slate-600 transition-opacity"
                      title="复制 Trace ID"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <rect x="9" y="9" width="13" height="13" rx="2" />
                        <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
                      </svg>
                    </button>
                  </div>
                </td>

                {/* Question */}
                <td className="py-3 px-4">
                  <span className="text-slate-700 line-clamp-1" title={t.question}>
                    {truncate(t.question, 50)}
                  </span>
                </td>

                {/* Answer preview */}
                <td className="py-3 px-4">
                  <span className="text-slate-500 line-clamp-1" title={t.answer_preview}>
                    {truncate(t.answer_preview, 50)}
                  </span>
                </td>

                {/* Duration */}
                <td className={`py-3 px-4 text-right font-mono tabular-nums font-semibold ${durationColor(t.duration_ms)}`}>
                  {t.duration_ms}ms
                </td>

                {/* Tokens */}
                <td className="py-3 px-4 text-right font-mono text-xs text-slate-500 tabular-nums">
                  {t.usage?.total_tokens ?? "--"}
                </td>

                {/* Session */}
                <td className="py-3 px-4">
                  <span className="text-xs font-mono text-slate-400 bg-slate-100 rounded px-1.5 py-0.5">
                    {t.session_id.slice(0, 16)}
                  </span>
                </td>

                {/* Time */}
                <td className="py-3 px-4 text-xs text-slate-400 font-mono">
                  {formatTime(t.timestamp)}
                </td>

                {/* Action */}
                <td className="py-3 px-4">
                  <span className="text-xs text-violet-600 font-medium opacity-0 group-hover:opacity-100 transition-opacity">
                    详情 →
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
