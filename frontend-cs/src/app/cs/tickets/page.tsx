"use client";

/**
 * /cs/tickets — 工单管理页（批次C）。
 *
 * 管理侧工单列表：按状态/类型过滤，支持状态流转（supervisor 权限，
 * 非法流转由后端 409 拦截并提示）。
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { TicketCheck } from "lucide-react";
import {
  listTickets,
  transitionTicket,
} from "@/api/cs";
import type { TicketDTO } from "@/types/cs";

const STATUS_BADGES: Record<string, { label: string; cls: string }> = {
  open: { label: "已受理", cls: "bg-blue-100 text-blue-700" },
  processing: { label: "处理中", cls: "bg-amber-100 text-amber-700" },
  pending_user: { label: "待用户补充", cls: "bg-violet-100 text-violet-700" },
  resolved: { label: "已解决", cls: "bg-emerald-100 text-emerald-700" },
  closed: { label: "已关闭", cls: "bg-slate-100 text-slate-500" },
};

const TYPE_LABELS: Record<string, string> = {
  complaint: "投诉",
  handoff: "人工服务",
  inquiry: "咨询",
  repair: "报修",
};

const PRIORITY_BADGES: Record<string, string> = {
  low: "bg-slate-100 text-slate-500",
  medium: "bg-blue-50 text-blue-600",
  high: "bg-orange-100 text-orange-700",
  critical: "bg-red-100 text-red-700",
};

// 各状态允许的下一步流转（与后端 VALID_TICKET_TRANSITIONS 对齐）
const NEXT_ACTIONS: Record<string, { status: string; label: string }[]> = {
  open: [
    { status: "processing", label: "开始处理" },
    { status: "pending_user", label: "待用户补充" },
    { status: "resolved", label: "标记解决" },
    { status: "closed", label: "关闭" },
  ],
  processing: [
    { status: "pending_user", label: "待用户补充" },
    { status: "resolved", label: "标记解决" },
    { status: "closed", label: "关闭" },
  ],
  pending_user: [
    { status: "processing", label: "恢复处理" },
    { status: "resolved", label: "标记解决" },
    { status: "closed", label: "关闭" },
  ],
  resolved: [{ status: "closed", label: "关闭" }],
  closed: [],
};

export default function TicketsPage() {
  const [tickets, setTickets] = useState<TicketDTO[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actingId, setActingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const res = await listTickets({
        status: statusFilter || undefined,
        type: typeFilter || undefined,
        limit: 100,
      });
      setTickets(res.items);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, typeFilter]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleTransition = async (ticket: TicketDTO, status: string) => {
    setActingId(ticket.ticket_id);
    setError(null);
    try {
      const updated = await transitionTicket(ticket.ticket_id, status);
      setTickets((prev) =>
        prev.map((t) => (t.ticket_id === updated.ticket_id ? updated : t)),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setActingId(null);
    }
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <TicketCheck size={20} className="text-blue-600" />
          <h1 className="text-lg font-medium">工单管理</h1>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-2 py-1.5 text-xs border border-slate-200 rounded-lg bg-white"
          >
            <option value="">全部状态</option>
            {Object.entries(STATUS_BADGES).map(([k, v]) => (
              <option key={k} value={k}>
                {v.label}
              </option>
            ))}
          </select>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="px-2 py-1.5 text-xs border border-slate-200 rounded-lg bg-white"
          >
            <option value="">全部类型</option>
            {Object.entries(TYPE_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && (
        <div className="mb-3 px-3 py-2 text-sm text-red-700 bg-red-50 rounded-lg">
          {error}
        </div>
      )}

      <div className="border border-slate-200 rounded-xl overflow-hidden">
        <div className="px-4 py-2.5 bg-slate-50 border-b border-slate-200 text-sm font-medium">
          工单列表（{tickets.length}）
        </div>
        {loading ? (
          <div className="px-4 py-10 text-sm text-slate-400 text-center">
            加载中...
          </div>
        ) : tickets.length === 0 ? (
          <div className="px-4 py-10 text-sm text-slate-400 text-center">
            暂无工单。投诉与转人工会自动生成工单。
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {tickets.map((t) => {
              const badge = STATUS_BADGES[t.status];
              const actions = NEXT_ACTIONS[t.status] ?? [];
              return (
                <div key={t.ticket_id} className="px-4 py-3">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs text-slate-500 font-mono">
                      {t.ticket_id}
                    </span>
                    <span className="text-[10px] bg-slate-100 text-slate-600 rounded px-1.5 py-0.5">
                      {TYPE_LABELS[t.type] ?? t.type}
                    </span>
                    <span
                      className={`text-[10px] rounded px-1.5 py-0.5 ${
                        PRIORITY_BADGES[t.priority] ?? ""
                      }`}
                    >
                      {t.priority}
                    </span>
                    {badge && (
                      <span
                        className={`text-[10px] rounded px-1.5 py-0.5 ${badge.cls}`}
                      >
                        {badge.label}
                      </span>
                    )}
                    <Link
                      href={`/cs/conversations/${t.conversation_id}`}
                      className="text-[10px] text-blue-600 hover:underline ml-auto"
                    >
                      查看会话
                    </Link>
                  </div>
                  <p className="text-sm text-slate-700 truncate mb-1.5">
                    {t.title || t.description || "（无描述）"}
                  </p>
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11px] text-slate-400">
                      用户 {t.user_id.slice(0, 12)} · 创建{" "}
                      {t.created_at.slice(0, 16).replace("T", " ")}
                    </span>
                    <div className="flex gap-1.5 ml-auto">
                      {actions.map((a) => (
                        <button
                          key={a.status}
                          disabled={actingId === t.ticket_id}
                          onClick={() => void handleTransition(t, a.status)}
                          className={`px-2 py-0.5 text-[11px] rounded-md border transition-colors disabled:opacity-50 ${
                            a.status === "closed"
                              ? "border-slate-300 text-slate-600 hover:bg-slate-100"
                              : "border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100"
                          }`}
                        >
                          {a.label}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
