"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { Headphones, Search, ChevronRight, MessageSquare, GitBranch } from "lucide-react";
import { listConversations } from "@/lib/api/cs";
import type { ConversationSummary, PaginatedConversations } from "@/types/cs";
import { formatRelative } from "@/types/trace";

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  open: { label: "进行中", color: "bg-emerald-100 text-emerald-700" },
  closed: { label: "已关闭", color: "bg-slate-100 text-slate-600" },
  escalated: { label: "已升级", color: "bg-amber-100 text-amber-700" },
};

const MODE_LABELS: Record<string, string> = {
  ai: "AI 处理",
  human: "人工处理",
  hybrid: "混合模式",
};

export default function ConversationsPage() {
  const [data, setData] = useState<PaginatedConversations | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [modeFilter, setModeFilter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [cursor, setCursor] = useState<string | undefined>();

  const fetchConversations = useCallback(async (reset = false) => {
    setLoading(true);
    setError(null);
    try {
      const result = await listConversations({
        limit: 20,
        cursor: reset ? undefined : cursor,
        status: statusFilter || undefined,
        handling_mode: modeFilter || undefined,
        q: searchQuery || undefined,
      });
      if (reset) {
        setData(result);
      } else {
        setData((prev) =>
          prev
            ? { ...result, items: [...prev.items, ...result.items] }
            : result,
        );
      }
      if (result.has_more && result.items.length > 0) {
        const last = result.items[result.items.length - 1];
        setCursor(last.last_activity_at || undefined);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [cursor, statusFilter, modeFilter, searchQuery]);

  useEffect(() => {
    fetchConversations(true);
  }, [statusFilter, modeFilter]);

  const handleSearch = () => {
    setCursor(undefined);
    fetchConversations(true);
  };

  const handleLoadMore = () => {
    fetchConversations(false);
  };

  return (
    <div className="flex-1 overflow-auto bg-surface-base">
      <div className="max-w-6xl mx-auto px-6 py-6 space-y-5">
        {/* Header */}
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-accent/10 flex items-center justify-center">
            <Headphones size={18} className="text-accent" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-text-primary">会话管理</h1>
            <p className="text-xs text-text-muted">客服对话记录与链路追踪</p>
          </div>
        </div>

        {/* Filter bar */}
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2 flex-1 min-w-[200px] bg-white border border-border-subtle rounded-lg px-3 py-2">
            <Search size={14} className="text-text-muted shrink-0" />
            <input
              type="text"
              placeholder="搜索会话摘要..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              className="flex-1 text-sm bg-transparent outline-none text-text-primary placeholder:text-text-muted"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setCursor(undefined); }}
            className="text-sm border border-border-subtle rounded-lg px-3 py-2 bg-white text-text-primary"
          >
            <option value="">全部状态</option>
            <option value="open">进行中</option>
            <option value="closed">已关闭</option>
            <option value="escalated">已升级</option>
          </select>
          <select
            value={modeFilter}
            onChange={(e) => { setModeFilter(e.target.value); setCursor(undefined); }}
            className="text-sm border border-border-subtle rounded-lg px-3 py-2 bg-white text-text-primary"
          >
            <option value="">全部模式</option>
            <option value="ai">AI 处理</option>
            <option value="human">人工处理</option>
            <option value="hybrid">混合模式</option>
          </select>
        </div>

        {/* Error */}
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700">
            加载失败: {error}
          </div>
        )}

        {/* Table */}
        {data && data.items.length > 0 && (
          <div className="bg-white border border-border-subtle rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border-subtle bg-slate-50/50">
                  <th className="text-left px-4 py-3 text-xs font-medium text-text-muted uppercase tracking-wider">会话</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-text-muted uppercase tracking-wider">用户</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-text-muted uppercase tracking-wider">状态</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-text-muted uppercase tracking-wider">模式</th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-text-muted uppercase tracking-wider">消息</th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-text-muted uppercase tracking-wider">链路</th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-text-muted uppercase tracking-wider">最近活动</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((conv) => {
                  const statusInfo = STATUS_LABELS[conv.conversation_status] || {
                    label: conv.conversation_status,
                    color: "bg-slate-100 text-slate-600",
                  };
                  return (
                    <tr key={conv.conversation_id} className="border-b border-border-subtle last:border-b-0 hover:bg-slate-50/50 transition-colors">
                      <td className="px-4 py-3">
                        <Link
                          href={`/cs/conversations/${conv.conversation_id}`}
                          className="font-mono text-xs text-accent hover:underline"
                        >
                          {conv.conversation_id.slice(0, 12)}
                        </Link>
                        {conv.summary && (
                          <p className="text-text-secondary text-xs mt-0.5 truncate max-w-[240px]">
                            {conv.summary}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-text-secondary font-mono text-xs">
                        {conv.user_id.slice(0, 12)}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${statusInfo.color}`}>
                          {statusInfo.label}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-text-secondary text-xs">
                        {MODE_LABELS[conv.handling_mode] || conv.handling_mode}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span className="inline-flex items-center gap-1 text-text-secondary text-xs">
                          <MessageSquare size={12} />
                          {conv.message_count}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span className="inline-flex items-center gap-1 text-text-secondary text-xs">
                          <GitBranch size={12} />
                          {conv.trace_count}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right text-text-muted text-xs">
                        {conv.last_activity_at ? formatRelative(conv.last_activity_at) : "--"}
                      </td>
                      <td className="px-4 py-3">
                        <Link
                          href={`/cs/conversations/${conv.conversation_id}`}
                          className="text-text-muted hover:text-accent transition-colors"
                        >
                          <ChevronRight size={16} />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {/* Load more */}
            {data.has_more && (
              <div className="border-t border-border-subtle px-4 py-3 text-center">
                <button
                  onClick={handleLoadMore}
                  disabled={loading}
                  className="text-xs text-accent hover:text-accent/80 disabled:opacity-50"
                >
                  {loading ? "加载中..." : "加载更多"}
                </button>
              </div>
            )}
          </div>
        )}

        {/* Empty state */}
        {!loading && data && data.items.length === 0 && (
          <div className="text-center py-16">
            <Headphones size={40} className="mx-auto text-text-muted/40 mb-3" />
            <p className="text-text-muted text-sm">暂无会话记录</p>
            <p className="text-text-muted/60 text-xs mt-1">客服对话开始后，会话将显示在此处</p>
          </div>
        )}

        {/* Loading skeleton */}
        {loading && !data && (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="bg-white border border-border-subtle rounded-xl h-16 animate-pulse" />
            ))}
          </div>
        )}

        {/* Total count */}
        {data && (
          <p className="text-xs text-text-muted text-right">
            共 {data.total} 个会话
          </p>
        )}
      </div>
    </div>
  );
}
