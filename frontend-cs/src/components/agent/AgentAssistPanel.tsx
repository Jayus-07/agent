"use client";

/**
 * AgentAssistPanel — 坐席辅助推荐面板（批次A）。
 *
 * 展示后端 assist.suggestion 推送的 top-k 候选回复（知识答案 / 知识库
 * 原文 / 订单速览），点击仅**填充输入框**，绝不自动发送。
 * 低置信推荐由后端在文本内附带"仅供参考"类标注。
 */

export type AssistSuggestion = {
  kind: string;
  source: string;
  score: number | null;
  text: string;
};

const KIND_META: Record<string, { icon: string; cls: string }> = {
  knowledge_answer: { icon: "💡", cls: "bg-violet-50 text-violet-600" },
  knowledge_snippet: { icon: "📄", cls: "bg-slate-100 text-slate-600" },
  order_summary: { icon: "📦", cls: "bg-blue-50 text-blue-600" },
};

export default function AgentAssistPanel({
  suggestions,
  onPick,
}: {
  suggestions: AssistSuggestion[];
  onPick: (text: string) => void;
}) {
  if (suggestions.length === 0) return null;

  return (
    <div className="border-t border-slate-100 bg-slate-50/60 px-3 pt-2 pb-1">
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="text-[10px] font-medium text-slate-500 uppercase tracking-wide">
          AI 推荐回复
        </span>
        <span className="text-[10px] text-slate-400">
          点击填入输入框，请确认后再发送
        </span>
      </div>
      <div className="space-y-1.5 max-h-40 overflow-y-auto">
        {suggestions.map((s, i) => {
          const meta = KIND_META[s.kind] ?? {
            icon: "💬",
            cls: "bg-slate-100 text-slate-600",
          };
          return (
            <button
              key={`${s.kind}-${i}`}
              onClick={() => onPick(s.text)}
              className="w-full text-left bg-white border border-slate-200 rounded-lg px-2.5 py-2
                hover:border-blue-300 hover:bg-blue-50/40 transition-colors group"
            >
              <div className="flex items-center gap-1.5 mb-1">
                <span
                  className={`text-[10px] rounded px-1.5 py-0.5 ${meta.cls}`}
                >
                  {meta.icon} {s.source}
                </span>
                {s.score != null && (
                  <span className="text-[10px] text-slate-400">
                    置信 {(s.score * 100).toFixed(0)}%
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-700 line-clamp-2 whitespace-pre-wrap break-words">
                {s.text}
              </p>
            </button>
          );
        })}
      </div>
    </div>
  );
}
