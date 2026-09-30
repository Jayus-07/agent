"use client";

import { useState } from "react";
import { evaluationService, type AppendResult } from "@/api/evaluation";

interface Props {
  traceId: string;
  question: string;
  onClose: () => void;
}

// 评测模块清单必须与后端 models.ModuleKind 一致（唯一事实源在后端）。
// 旧版这里只写了 5 个（漏 travel 系整套），与后端 9 个口径不一致——
// 漂移由 backend/tests/evaluation/test_module_kind_single_source.py 在 CI 拦截。
const MODULES = [
  "planner",
  "rag",
  "cs",
  "sql",
  "e2e",
  "travel",
  "travel-provider",
  "travel-commerce",
  "travel-booking",
] as const;

export default function AddToEvalModal({ traceId, question, onClose }: Props) {
  const [module, setModule] = useState<string>("");
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AppendResult | null>(null);
  const [error, setError] = useState("");

  const handleSubmit = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await evaluationService.createFromTrace({
        trace_id: traceId,
        module: module || undefined,
        note: note || undefined,
      });
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-slate-800">加入评测集</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-lg">&times;</button>
        </div>

        {result ? (
          <div className="space-y-3">
            <div className={`rounded-lg p-3 text-sm ${result.appended ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
              {result.appended ? (
                <>已添加为 <span className="font-mono font-semibold">{result.case_id}</span></>
              ) : (
                <>跳过: {result.reason}</>
              )}
            </div>
            <button
              onClick={onClose}
              className="w-full py-2 rounded-lg bg-slate-100 text-slate-600 text-sm hover:bg-slate-200"
            >
              关闭
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1">问题</label>
              <p className="text-sm text-slate-700 bg-slate-50 rounded-lg p-2 line-clamp-2">{question || "(无)"}</p>
            </div>

            <div>
              <label className="block text-xs text-slate-500 mb-1">目标模块（留空自动推断）</label>
              <select
                value={module}
                onChange={(e) => setModule(e.target.value)}
                className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700"
              >
                <option value="">自动推断</option>
                {MODULES.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs text-slate-500 mb-1">备注</label>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="可选备注..."
                className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
              />
            </div>

            {error && <p className="text-sm text-red-600">{error}</p>}

            <div className="flex gap-2">
              <button
                onClick={onClose}
                className="flex-1 py-2 rounded-lg bg-slate-100 text-slate-600 text-sm hover:bg-slate-200"
              >
                取消
              </button>
              <button
                onClick={handleSubmit}
                disabled={loading}
                className="flex-1 py-2 rounded-lg bg-violet-600 text-white text-sm hover:bg-violet-700 disabled:opacity-50"
              >
                {loading ? "提交中..." : "确认添加"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
