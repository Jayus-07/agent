"use client";

import { useState } from "react";

interface Props {
  question: string;
  answer: string;
  error?: Record<string, unknown>;
}

export default function InputOutputPanel({ question, answer, error }: Props) {
  const [expanded, setExpanded] = useState<"input" | "output" | "both" | null>(
    Object.keys(error ?? {}).length > 0 ? "output" : "both"
  );
  const hasError = error && Object.keys(error).length > 0;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      {/* Input */}
      <div className="border border-slate-200 rounded-xl overflow-hidden">
        <button
          onClick={() => setExpanded(expanded === "input" || expanded === "both" ? null : (expanded === "output" ? "both" : "input"))}
          className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 hover:bg-slate-100 transition-colors text-left"
        >
          <span className="text-xs font-medium text-slate-600 uppercase tracking-wider">
            👤 用户问题
          </span>
          <span className="text-[10px] text-slate-400 font-mono">
            {question.length} 字符
          </span>
        </button>
        {(expanded === "input" || expanded === "both") && (
          <div className="px-4 py-3 bg-white">
            <pre className="text-sm text-slate-700 whitespace-pre-wrap font-sans leading-relaxed">
              {question || "--"}
            </pre>
          </div>
        )}
      </div>

      {/* Output */}
      <div className={`border rounded-xl overflow-hidden ${hasError ? "border-red-300" : "border-slate-200"}`}>
        <button
          onClick={() => setExpanded(expanded === "output" || expanded === "both" ? null : (expanded === "input" ? "both" : "output"))}
          className={`w-full flex items-center justify-between px-4 py-3 hover:bg-slate-100 transition-colors text-left ${
            hasError ? "bg-red-50 hover:bg-red-100" : "bg-slate-50"
          }`}
        >
          <span className="text-xs font-medium text-slate-600 uppercase tracking-wider">
            🤖 模型回答 {hasError && <span className="text-red-500 ml-1">(含错误)</span>}
          </span>
          <span className="text-[10px] text-slate-400 font-mono">
            {answer.length} 字符
          </span>
        </button>
        {(expanded === "output" || expanded === "both") && (
          <div className="px-4 py-3 bg-white">
            {/* Error stack */}
            {hasError && (
              <div className="mb-3 bg-red-50 border border-red-200 rounded-lg p-3">
                <p className="text-xs font-semibold text-red-600 mb-1">
                  {(error as Record<string, string>).code ?? "Error"}
                </p>
                <pre className="text-xs text-red-500 whitespace-pre-wrap font-mono leading-relaxed">
                  {(error as Record<string, string>).message ?? JSON.stringify(error, null, 2)}
                </pre>
              </div>
            )}
            <pre className="text-sm text-slate-700 whitespace-pre-wrap font-sans leading-relaxed">
              {answer || "--"}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
