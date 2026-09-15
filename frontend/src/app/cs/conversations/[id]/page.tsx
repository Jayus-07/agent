"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  Headphones, ArrowLeft, MessageSquare, GitBranch,
  Eye, User, Bot, Clock, AlertCircle,
} from "lucide-react";
import { getConversation, getConversationTraces } from "@/api/cs";
import { getTraceById } from "@/lib/observability/source";
import TraceDetailPanels from "@/components/observability/trace/TraceDetailPanels";
import type { ConversationDetail, MessageDTO } from "@/types/cs";
import type { TraceRecord } from "@/types/trace";
import { formatRelative, formatTime, statusBadge } from "@/types/trace";

const INTENT_LABELS: Record<string, string> = {
  order: "订单查询",
  logistics: "物流追踪",
  refund: "退款申请",
  complaint: "投诉建议",
  product: "商品咨询",
  general: "通用问答",
};

export default function ConversationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [conv, setConv] = useState<ConversationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedTrace, setSelectedTrace] = useState<TraceRecord | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const c = await getConversation(id);
        if (!cancelled) {
          setConv(c);
          setLoading(false);
        }
      } catch (e) {
        if (!cancelled) {
          setTraceError((e as Error).message);
          setLoading(false);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [id]);

  const handleViewTrace = async (traceId: string) => {
    if (selectedTrace?.id === traceId) {
      setSelectedTrace(null);
      return;
    }
    setTraceLoading(true);
    setTraceError(null);
    try {
      const t = await getTraceById(traceId);
      setSelectedTrace(t);
      if (!t) setTraceError(`Trace ${traceId.slice(0, 8)} 不存在或已过期`);
    } catch (e) {
      setTraceError((e as Error).message);
    } finally {
      setTraceLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center bg-surface-base">
        <div className="text-sm text-text-muted">加载中...</div>
      </div>
    );
  }

  if (!conv) {
    return (
      <div className="flex-1 flex items-center justify-center bg-surface-base">
        <div className="text-center">
          <p className="text-text-muted text-lg">会话不存在</p>
          <button
            onClick={() => router.push("/cs/conversations")}
            className="mt-3 text-sm text-accent hover:underline"
          >
            返回列表
          </button>
        </div>
      </div>
    );
  }

  const messagesWithTrace = conv.messages.filter((m) => m.trace_id);

  return (
    <div className="flex-1 overflow-auto bg-surface-base">
      <div className="max-w-[1600px] mx-auto px-6 py-6 space-y-5">
        {/* Breadcrumb + Header */}
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push("/cs/conversations")}
            className="text-text-muted hover:text-text-primary transition-colors"
          >
            <ArrowLeft size={18} />
          </button>
          <div className="w-9 h-9 rounded-xl bg-accent/10 flex items-center justify-center">
            <Headphones size={18} className="text-accent" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold text-text-primary font-mono">
                {conv.conversation_id.slice(0, 16)}
              </h1>
              <span className="text-[10px] text-text-muted bg-slate-100 rounded px-2 py-0.5 font-mono">
                {conv.channel}
              </span>
            </div>
            <p className="text-xs text-text-muted">
              用户 {conv.user_id} &middot; {formatRelative(conv.created_at)}
            </p>
          </div>
          <div className="flex items-center gap-3 text-xs text-text-secondary">
            <span className="flex items-center gap-1">
              <MessageSquare size={12} />
              {conv.messages.length} 消息
            </span>
            <span className="flex items-center gap-1">
              <GitBranch size={12} />
              {conv.trace_count} 链路
            </span>
          </div>
        </div>

        {/* Conversation metadata */}
        <div className="bg-white border border-border-subtle rounded-xl p-4">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4 text-sm">
            <div>
              <span className="text-text-muted text-xs">状态</span>
              <p className="text-text-primary font-medium mt-0.5">{conv.conversation_status}</p>
            </div>
            <div>
              <span className="text-text-muted text-xs">处理模式</span>
              <p className="text-text-primary font-medium mt-0.5">{conv.handling_mode}</p>
            </div>
            <div>
              <span className="text-text-muted text-xs">优先级</span>
              <p className="text-text-primary font-medium mt-0.5">{conv.priority}</p>
            </div>
            <div>
              <span className="text-text-muted text-xs">最近活动</span>
              <p className="text-text-primary font-mono text-xs mt-0.5">
                {conv.last_activity_at ? formatRelative(conv.last_activity_at) : "--"}
              </p>
            </div>
            <div>
              <span className="text-text-muted text-xs">最后链路</span>
              <p className="text-text-primary font-mono text-xs mt-0.5">
                {conv.last_trace_id ? conv.last_trace_id.slice(0, 12) : "--"}
              </p>
            </div>
          </div>
          {conv.summary && (
            <div className="mt-3 pt-3 border-t border-border-subtle">
              <span className="text-text-muted text-xs">摘要</span>
              <p className="text-text-secondary text-sm mt-1">{conv.summary}</p>
            </div>
          )}
        </div>

        {/* Two-pane layout */}
        <div className={`grid gap-5 ${selectedTrace ? "grid-cols-1 lg:grid-cols-2" : "grid-cols-1"}`}>
          {/* Left: Message thread */}
          <div className="space-y-3">
            <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider flex items-center gap-2">
              <MessageSquare size={12} />
              对话记录
            </h2>
            <div className="space-y-2">
              {conv.messages.map((msg) => (
                <MessageRow
                  key={msg.message_id}
                  message={msg}
                  isSelected={selectedTrace?.id === msg.trace_id}
                  onViewTrace={msg.trace_id ? () => handleViewTrace(msg.trace_id!) : undefined}
                  traceLoading={traceLoading && selectedTrace?.id !== msg.trace_id}
                />
              ))}
            </div>
          </div>

          {/* Right: Trace panel */}
          {selectedTrace && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider flex items-center gap-2">
                  <GitBranch size={12} />
                  链路详情
                </h2>
                <div className="flex items-center gap-2">
                  <Link
                    href={`/observability/traces/${selectedTrace.id}`}
                    className="text-[10px] text-accent hover:underline"
                    target="_blank"
                  >
                    在新页面打开
                  </Link>
                  <button
                    onClick={() => setSelectedTrace(null)}
                    className="text-xs text-text-muted hover:text-text-primary"
                  >
                    关闭
                  </button>
                </div>
              </div>
              <div className="bg-slate-50 border border-border-subtle rounded-xl p-4 overflow-auto max-h-[calc(100vh-320px)]">
                <TraceDetailPanels trace={selectedTrace} />
              </div>
            </div>
          )}

          {/* Trace loading placeholder */}
          {traceLoading && !selectedTrace && (
            <div className="flex items-center justify-center py-16">
              <div className="text-sm text-text-muted">加载链路数据...</div>
            </div>
          )}

          {/* Trace error */}
          {traceError && !traceLoading && (
            <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700 flex items-center gap-2">
              <AlertCircle size={14} />
              {traceError}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function MessageRow({
  message,
  isSelected,
  onViewTrace,
  traceLoading,
}: {
  message: MessageDTO;
  isSelected: boolean;
  onViewTrace?: () => void;
  traceLoading: boolean;
}) {
  const isUser = message.sender_type === "user";
  const intentLabel = message.intent_domain
    ? INTENT_LABELS[message.intent_domain] || message.intent_domain
    : null;

  return (
    <div
      className={`bg-white border rounded-xl p-4 transition-colors ${
        isSelected
          ? "border-accent ring-1 ring-accent/20"
          : "border-border-subtle hover:border-border-subtle/80"
      }`}
    >
      <div className="flex items-start gap-3">
        {/* Avatar */}
        <div className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 ${
          isUser ? "bg-accent/10 text-accent" : "bg-slate-100 text-slate-500"
        }`}>
          {isUser ? <User size={14} /> : <Bot size={14} />}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-medium text-text-primary">
              {isUser ? "用户" : "客服"}
            </span>
            {intentLabel && (
              <span className="text-[10px] bg-violet-50 text-violet-600 rounded px-1.5 py-0.5">
                {intentLabel}
              </span>
            )}
            {message.confidence != null && (
              <span className="text-[10px] text-text-muted">
                {(message.confidence * 100).toFixed(0)}%
              </span>
            )}
            <span className="text-[10px] text-text-muted ml-auto flex items-center gap-1">
              <Clock size={10} />
              {formatRelative(message.created_at)}
            </span>
          </div>
          <p className="text-sm text-text-secondary whitespace-pre-wrap break-words">
            {message.content}
          </p>

          {/* Trace link */}
          {message.trace_id && onViewTrace && (
            <div className="mt-2 pt-2 border-t border-border-subtle/50">
              <button
                onClick={onViewTrace}
                disabled={traceLoading}
                className={`inline-flex items-center gap-1.5 text-[11px] rounded-md px-2.5 py-1 transition-colors ${
                  isSelected
                    ? "bg-accent text-white hover:bg-accent/90"
                    : "bg-slate-50 text-accent hover:bg-accent/10 border border-border-subtle"
                } disabled:opacity-50`}
              >
                <Eye size={12} />
                {isSelected ? "隐藏链路" : "查看链路"}
              </button>
              <span className="text-[10px] text-text-muted ml-2 font-mono">
                {message.trace_id.slice(0, 12)}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
