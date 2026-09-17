"use client";

/**
 * /cs/handoff — 人工接入坐席工作台（v1）
 *
 * 机制：2s 轮询队列 + 消息增量拉取（since_id）。
 * 企业标准为 WebSocket/SSE 推送坐席队列，v1 用轮询保证实现简单与网关兼容；
 * 演进路径见 docs/customer-service/演示沙盒方案-2026-09-17.md §七。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Headphones, Send, UserRound } from "lucide-react";
import {
  claimConversation,
  getHandoffMessages,
  getHandoffQueue,
  sendAgentMessage,
} from "@/api/cs";
import type {
  HandoffMessageDTO,
  HandoffQueueItem,
} from "@/types/cs";

const STATE_BADGES: Record<string, { label: string; cls: string }> = {
  handoff_requested: { label: "已请求", cls: "bg-amber-100 text-amber-700" },
  waiting_human: { label: "排队中", cls: "bg-red-100 text-red-700" },
  human_active: { label: "人工处理中", cls: "bg-emerald-100 text-emerald-700" },
};

const SENDER_LABELS: Record<string, string> = {
  user: "用户",
  assistant: "AI",
  human_agent: "坐席",
  system: "系统",
};

const AGENT_ID_KEY = "cs_handoff_agent_id";

export default function HandoffWorkbenchPage() {
  const [agentId, setAgentId] = useState("");
  const [queue, setQueue] = useState<HandoffQueueItem[]>([]);
  const [selected, setSelected] = useState<HandoffQueueItem | null>(null);
  const [messages, setMessages] = useState<HandoffMessageDTO[]>([]);
  const [reply, setReply] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const sinceIdRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  // agent_id 持久化
  useEffect(() => {
    setAgentId(localStorage.getItem(AGENT_ID_KEY) ?? "");
  }, []);
  useEffect(() => {
    if (agentId) localStorage.setItem(AGENT_ID_KEY, agentId);
  }, [agentId]);

  // 队列轮询（2s）
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const res = await getHandoffQueue();
        if (alive) setQueue(res.items);
      } catch {
        // 轮询失败静默，下轮重试
      }
    };
    tick();
    const timer = setInterval(tick, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // 选中会话：重置增量游标
  const selectConversation = useCallback((item: HandoffQueueItem) => {
    setSelected(item);
    setMessages([]);
    sinceIdRef.current = 0;
  }, []);

  // 消息增量轮询（仅选中会话时，2s）
  useEffect(() => {
    if (!selected) return;
    let alive = true;
    const tick = async () => {
      try {
        const res = await getHandoffMessages(
          selected.conversation_id,
          sinceIdRef.current,
        );
        if (!alive) return;
        if (res.messages.length > 0) {
          setMessages((prev) => [...prev, ...res.messages]);
          sinceIdRef.current = res.last_id;
        }
      } catch {
        // 静默重试
      }
    };
    tick();
    const timer = setInterval(tick, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [selected]);

  // 自动滚动到底
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleClaim = async (item: HandoffQueueItem) => {
    if (!agentId.trim()) {
      setError("请先填写坐席 ID");
      return;
    }
    setError(null);
    try {
      await claimConversation(item.conversation_id, agentId.trim());
      selectConversation(item);
      setQueue((prev) =>
        prev.map((q) =>
          q.conversation_id === item.conversation_id
            ? { ...q, handoff_state: "human_active" }
            : q,
        ),
      );
    } catch (e) {
      const msg = (e as Error).message ?? "认领失败";
      setError(msg.includes("409") ? "该会话尚未进入排队，请稍后再试" : msg);
    }
  };

  const handleSend = async () => {
    if (!selected || !reply.trim() || sending) return;
    if (!agentId.trim()) {
      setError("请先填写坐席 ID");
      return;
    }
    setSending(true);
    setError(null);
    try {
      await sendAgentMessage(
        selected.conversation_id,
        agentId.trim(),
        reply.trim(),
      );
      // 全量拉一次，拿到落库后的自增 id 作为增量游标
      const res = await getHandoffMessages(
        selected.conversation_id,
        0,
      );
      setMessages(res.messages);
      sinceIdRef.current = res.last_id;
      setReply("");
    } catch (e) {
      const msg = (e as Error).message ?? "发送失败";
      setError(msg.includes("409") ? "请先认领该会话" : msg);
    } finally {
      setSending(false);
    }
  };

  const stateBadge = (state: string) => {
    const cfg = STATE_BADGES[state];
    return cfg ? (
      <span className={`px-1.5 py-0.5 rounded text-[10px] ${cfg.cls}`}>
        {cfg.label}
      </span>
    ) : (
      <span className="px-1.5 py-0.5 rounded text-[10px] bg-slate-100 text-slate-600">
        {state}
      </span>
    );
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Headphones size={20} className="text-blue-600" />
          <h1 className="text-lg font-medium">人工接入坐席工作台</h1>
        </div>
        <div className="flex items-center gap-2">
          <UserRound size={14} className="text-slate-500" />
          <input
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            placeholder="坐席 ID（如 agent-01）"
            className="w-44 px-2 py-1.5 text-sm border border-slate-200 rounded-lg
              focus:outline-none focus:border-blue-400"
          />
        </div>
      </div>

      {error && (
        <div className="mb-3 px-3 py-2 text-sm text-red-700 bg-red-50 rounded-lg">
          {error}
        </div>
      )}

      <div className="grid grid-cols-5 gap-4">
        {/* 队列 */}
        <div className="col-span-2 border border-slate-200 rounded-xl overflow-hidden">
          <div className="px-4 py-2.5 bg-slate-50 border-b border-slate-200 text-sm font-medium">
            待接入队列（{queue.length}）
          </div>
          <div className="max-h-[60vh] overflow-y-auto divide-y divide-slate-100">
            {queue.length === 0 && (
              <div className="px-4 py-10 text-sm text-slate-400 text-center">
                暂无进行中的人工转接会话
              </div>
            )}
            {queue.map((item) => (
              <div
                key={item.conversation_id}
                className={`px-4 py-3 cursor-pointer hover:bg-slate-50 transition-colors ${
                  selected?.conversation_id === item.conversation_id
                    ? "bg-blue-50/60"
                    : ""
                }`}
                onClick={() => selectConversation(item)}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-slate-500 font-mono">
                    {item.conversation_id.slice(0, 12)}…
                  </span>
                  {stateBadge(item.handoff_state)}
                </div>
                <p className="text-sm text-slate-700 truncate mb-1.5">
                  {item.last_message_preview ?? "（无消息）"}
                </p>
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-slate-400">
                    {item.trigger_reason ?? item.trigger_type ?? "转人工"}
                  </span>
                  {item.handoff_state === "waiting_human" && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleClaim(item);
                      }}
                      className="px-2.5 py-1 text-xs rounded-lg bg-blue-600 text-white
                        hover:bg-blue-700 transition-colors"
                    >
                      认领
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 会话详情 */}
        <div className="col-span-3 border border-slate-200 rounded-xl flex flex-col h-[70vh]">
          {!selected ? (
            <div className="flex-1 flex items-center justify-center text-sm text-slate-400">
              从左侧选择一个会话
            </div>
          ) : (
            <>
              <div className="px-4 py-2.5 bg-slate-50 border-b border-slate-200
                flex items-center justify-between">
                <span className="text-sm font-medium font-mono">
                  {selected.conversation_id.slice(0, 18)}…
                </span>
                {stateBadge(selected.handoff_state)}
              </div>
              <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2.5">
                {messages.map((m) => (
                  <div
                    key={m.message_id}
                    className={`flex ${
                      m.sender_type === "human_agent"
                        ? "justify-end"
                        : "justify-start"
                    }`}
                  >
                    <div
                      className={`max-w-[75%] px-3 py-2 rounded-xl text-sm ${
                        m.sender_type === "human_agent"
                          ? "bg-blue-600 text-white"
                          : m.sender_type === "user"
                            ? "bg-slate-100 text-slate-800"
                            : "bg-white border border-slate-200 text-slate-700"
                      }`}
                    >
                      <div className="text-[10px] opacity-70 mb-0.5">
                        {SENDER_LABELS[m.sender_type] ?? m.sender_type}
                      </div>
                      <div className="whitespace-pre-wrap break-words">
                        {m.content}
                      </div>
                    </div>
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>
              <div className="border-t border-slate-200 p-3 flex gap-2">
                <input
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleSend()}
                  placeholder={
                    selected.handoff_state === "human_active"
                      ? "输入回复内容，回车发送"
                      : "需先认领会话（waiting_human 状态）"
                  }
                  disabled={selected.handoff_state !== "human_active"}
                  className="flex-1 px-3 py-2 text-sm border border-slate-200 rounded-lg
                    focus:outline-none focus:border-blue-400 disabled:bg-slate-50
                    disabled:text-slate-400"
                />
                <button
                  onClick={handleSend}
                  disabled={sending || selected.handoff_state !== "human_active"}
                  className="px-3 py-2 rounded-lg bg-blue-600 text-white
                    hover:bg-blue-700 disabled:bg-slate-300 transition-colors"
                >
                  <Send size={16} />
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
