"use client";

import { useState, useEffect, useCallback } from "react";
import { Loader2, RefreshCw, Search, Plus, Trash2, Power, PowerOff } from "lucide-react";
import { useToast } from "@/components/shared/Toast";

interface KeywordRule {
  id: number; keyword: string; doc_type: string; category: string;
  weight: number; enabled: number; source: string;
}

const DOC_TYPE_LABELS: Record<string, string> = {
  general: "通用", faq: "FAQ", product_spec: "产品规格",
  policy: "政策制度", compliance: "合规法规", legal: "法律合同",
  listing: "Listing", sop: "SOP", ad_policy: "广告政策", training: "培训",
};

export default function KeywordsPage() {
  const toast = useToast();
  const [items, setItems] = useState<KeywordRule[]>([]);
  const [docTypes, setDocTypes] = useState<string[]>([]);
  const [activeType, setActiveType] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ keyword: "", doc_type: "general", category: "", weight: 1 });

  const fetchItems = useCallback(async () => {
    setLoading(true);
    try {
      const qs = new URLSearchParams();
      if (activeType) qs.set("doc_type", activeType);
      if (search) qs.set("search", search);
      const res = await fetch(`/api/rag/keywords?${qs}`);
      setItems((await res.json()).items || []);
    } catch {
    } finally { setLoading(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeType, search]);

  const fetchDocTypes = async () => {
    try {
      const res = await fetch("/api/rag/keywords/doc-types");
      setDocTypes((await res.json()).doc_types || []);
    } catch { /* */ }
  };

  useEffect(() => { fetchItems(); fetchDocTypes(); }, [fetchItems]);

  const upsert = async (kw: string, dt: string, cat: string, w: number) => {
    await fetch("/api/rag/keywords", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keyword: kw.trim(), doc_type: dt, category: cat, weight: w, enabled: 1 }),
    });
    fetchItems();
  };

  const toggle = async (kw: string, enabled: number) => {
    await fetch(`/api/rag/keywords/${encodeURIComponent(kw)}/toggle`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    fetchItems();
  };

  const remove = async (kw: string) => {
    await fetch(`/api/rag/keywords/${encodeURIComponent(kw)}`, { method: "DELETE" });
    fetchItems();
    toast.info(`已删除: ${kw}`);
  };

  const handleSave = async () => {
    if (!form.keyword.trim()) return;
    await upsert(form.keyword, form.doc_type, form.category, form.weight);
    setForm({ keyword: "", doc_type: activeType || "general", category: "", weight: 1 });
    setShowForm(false);
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* 头部 */}
        <div className="flex items-center justify-between mb-5">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">关键词规则管理</h1>
            <p className="text-xs text-text-muted mt-1"> 按文档类型分组维护关键词，修改后 60s 内自动生效</p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => { setForm({ ...form, doc_type: activeType || "general" }); setShowForm(true); }}
              className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors">
              <Plus size={12} /> 新增
            </button>
            <button onClick={fetchItems} disabled={loading}
              className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-secondary hover:bg-surface-hover disabled:opacity-40">
              {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            </button>
          </div>
        </div>

        <div className="flex gap-5">
          {/* 左侧：文档类型列表 */}
          <div className="w-44 shrink-0">
            <div className="bg-surface-base rounded-xl border border-border-subtle overflow-hidden">
              <div className="px-4 py-2.5 border-b border-border-subtle">
                <span className="text-xs font-medium text-text-primary">文档类型</span>
              </div>
              <div className="divide-y divide-border-subtle">
                <button
                  onClick={() => setActiveType("")}
                  className={`w-full text-left px-4 py-2 text-xs transition-colors ${!activeType ? "bg-accent/10 text-accent font-medium" : "text-text-secondary hover:bg-surface-hover"}`}
                >
                  全部类型
                </button>
                {docTypes.map((dt) => (
                  <button key={dt}
                    onClick={() => setActiveType(dt)}
                    className={`w-full text-left px-4 py-2 text-xs flex items-center justify-between transition-colors ${activeType === dt ? "bg-accent/10 text-accent font-medium" : "text-text-secondary hover:bg-surface-hover"}`}
                  >
                    <span>{DOC_TYPE_LABELS[dt] || dt}</span>
                    <span className="text-[10px] text-text-muted">{dt}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* 右侧：关键词列表 */}
          <div className="flex-1 min-w-0 space-y-3">
            {/* 搜索 + 新增表单 */}
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5 bg-surface-base border border-border-subtle rounded-lg px-2.5 py-1.5 flex-1 max-w-xs">
                <Search size={12} className="text-text-muted shrink-0" />
                <input value={search} onChange={e => setSearch(e.target.value)}
                  placeholder="搜索关键词…" className="bg-transparent text-xs text-text-primary outline-none w-full" />
              </div>
            </div>

            {showForm && (
              <div className="bg-surface-base rounded-xl border border-border-subtle p-4 flex items-end gap-3 flex-wrap">
                <div>
                  <label className="text-[10px] text-text-muted block mb-1">文档类型</label>
                  <select value={form.doc_type} onChange={e => setForm({ ...form, doc_type: e.target.value })}
                    className="text-xs rounded-lg border border-border-subtle px-2 py-1.5 outline-none bg-white">
                    {docTypes.map(dt => <option key={dt} value={dt}>{DOC_TYPE_LABELS[dt] || dt}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-text-muted block mb-1">关键词</label>
                  <input value={form.keyword} onChange={e => setForm({ ...form, keyword: e.target.value })}
                    className="w-32 text-xs rounded-lg border border-border-subtle px-2 py-1.5 outline-none" placeholder="如: DDP" />
                </div>
                <div>
                  <label className="text-[10px] text-text-muted block mb-1">分类</label>
                  <input value={form.category} onChange={e => setForm({ ...form, category: e.target.value })}
                    className="w-24 text-xs rounded-lg border border-border-subtle px-2 py-1.5 outline-none" placeholder="如: 物流追踪" />
                </div>
                <div>
                  <label className="text-[10px] text-text-muted block mb-1">权重</label>
                  <input type="number" min={1} max={10} value={form.weight}
                    onChange={e => setForm({ ...form, weight: parseInt(e.target.value) || 1 })}
                    className="w-16 text-xs rounded-lg border border-border-subtle px-2 py-1.5 outline-none" />
                </div>
                <button onClick={handleSave} className="px-3 py-1.5 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover">保存</button>
                <button onClick={() => setShowForm(false)} className="px-3 py-1.5 text-xs rounded-lg border border-border-subtle text-text-secondary">取消</button>
              </div>
            )}

            {/* 表格 */}
            <div className="bg-surface-base rounded-xl border border-border-subtle overflow-hidden">
              {loading && <div className="p-3 text-center text-xs text-text-muted animate-pulse">加载中...</div>}
              <table className="w-full text-xs">
                <thead><tr className="border-b border-border-subtle bg-surface-elevated text-text-muted">
                  {["关键词", "文档类型", "分类", "权重", "来源", "状态", "操作"].map(h => <th key={h} className="text-left px-4 py-2.5 font-medium">{h}</th>)}
                </tr></thead>
                <tbody>
                  {items.map((r) => (
                    <tr key={r.id} className="border-b border-border-subtle hover:bg-surface-hover">
                      <td className="px-4 py-2 font-mono text-text-primary">{r.keyword}</td>
                      <td className="px-4 py-2">
                        <span className="px-1.5 py-0.5 rounded text-[10px] bg-blue-50 text-blue-600">{DOC_TYPE_LABELS[r.doc_type] || r.doc_type}</span>
                      </td>
                      <td className="px-4 py-2">{r.category ? <span className="text-text-muted text-[10px]">{r.category}</span> : <span className="text-text-muted">-</span>}</td>
                      <td className="px-4 py-2 text-text-muted">{r.weight}</td>
                      <td className="px-4 py-2">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${r.source === "seed" ? "bg-slate-100 text-slate-500" : "bg-green-50 text-green-600"}`}>
                          {r.source === "seed" ? "系统" : "手动"}
                        </span>
                      </td>
                      <td className="px-4 py-2">
                        <button onClick={() => toggle(r.keyword, r.enabled ? 0 : 1)}
                          className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] ${r.enabled ? "bg-green-50 text-green-600" : "bg-red-50 text-red-400"}`}>
                          {r.enabled ? <Power size={10} /> : <PowerOff size={10} />}
                        </button>
                      </td>
                      <td className="px-4 py-2">
                        <button onClick={() => remove(r.keyword)} className="text-text-muted hover:text-red-500" title="删除"><Trash2 size={12} /></button>
                      </td>
                    </tr>
                  ))}
                  {!loading && items.length === 0 && (
                    <tr><td colSpan={7} className="px-4 py-8 text-center text-text-muted">暂无数据</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
