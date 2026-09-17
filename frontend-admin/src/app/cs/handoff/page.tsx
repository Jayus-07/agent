"use client";

/**
 * /cs/handoff — 人工接入坐席工作台（v2）
 *
 * 下行主通道：WebSocket /ws/cs/agent（ticket 一次性鉴权，断线指数退避重连）。
 * 降级：WS 未连接时回退 2s 轮询（transport fallback，不进业务逻辑）。
 * 上行：全走 HTTP（认领 / 发消息 / 关闭，X-API-Key 由 BFF 服务端注入）。
 * 事件语义见 docs/customer-service/演示沙盒方案-2026-09-17.md §七。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { BellRing, Headphones, Send, UserRound, Volume2, VolumeX, XCircle } from "lucide-react";
import {
  claimConversation,
  closeConversation,
  getHandoffMessages,
  getHandoffQueue,
  sendAgentMessage,
} from "@/api/cs";
import { useAgentSocket, type AgentEvent } from "@/lib/csAgentWs";
import type {
  HandoffMessageDTO,
  HandoffQueueItem,
} from "@/types/cs";

const STATE_BADGES: Record<string, { label: string; cls: string }> = {
  handoff_requested: { label: "已请求", cls: "bg-amber-100 text-amber-700" },
  waiting_human: { label: "排队中", cls: "bg-red-100 text-red-700" },
  human_active: { label: "人工处理中", cls: "bg-emerald-100 text-emerald-700" },
  closed: { label: "已结束", cls: "bg-slate-100 text-slate-500" },
};

const SENDER_LABELS: Record<string, string> = {
  user: "用户",
  assistant: "AI",
  human_agent: "坐席",
  system: "系统",
};

const AGENT_ID_KEY = "cs_handoff_agent_id";
const MUTE_KEY = "cs_handoff_muted";

// ── 通知基建（模块级，纯浏览器 API）──────────────────────
// 提示音：WebAudio 双 beep，无音频资源依赖。浏览器自动播放策略下
// 首次用户手势前可能被拦，静默失败即可（toast/桌面通知不受影响）。
let audioCtx: AudioContext | null = null;
function playBeep() {
  try {
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!Ctor) return;
    audioCtx = audioCtx ?? new Ctor();
    if (audioCtx.state === "suspended") {
      void audioCtx.resume().catch(() => undefined);
    }
    const beepAt = (delayMs: number) => {
      const osc = audioCtx!.createOscillator();
      const gain = audioCtx!.createGain();
      osc.connect(gain);
      gain.connect(audioCtx!.destination);
      osc.type = "sine";
      osc.frequency.value = 880;
      const t0 = audioCtx!.currentTime + delayMs;
      gain.gain.setValueAtTime(0.001, t0);
      gain.gain.exponentialRampToValueAtTime(0.18, t0 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, t0 + 0.45);
      osc.start(t0);
      osc.stop(t0 + 0.5);
    };
    beepAt(0);
    beepAt(0.28);
  } catch {
    // 静默：声音只是提醒增强手段
  }
}

// 桌面通知：仅在页面不可见时发（可见时 toast 已足够），点击聚焦窗口
function systemNotify(title: string, body: string) {
  try {
    if (
      typeof Notification === "undefined" ||
      Notification.permission !== "granted" ||
      document.visibilityState === "visible"
    ) {
      return;
    }
    const n = new Notification(title, { body, tag: "cs-handoff" });
    n.onclick = () => {
      window.focus();
      n.close();
    };
  } catch {
    // 静默
  }
}

type WaitingToast = {
  id: number;
  conversationId: string;
  preview: string;
  reason: string;
};

export default function HandoffWorkbenchPage() {
  const [agentId, setAgentId] = useState("");
  const [queue, setQueue] = useState<HandoffQueueItem[]>([]);
  const [selected, setSelected] = useState<HandoffQueueItem | null>(null);
  const [messages, setMessages] = useState<HandoffMessageDTO[]>([]);
  const [reply, setReply] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const sinceIdRef = useRef(0);
  const selectedRef = useRef<HandoffQueueItem | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  selectedRef.current = selected;

  // agent_id 持久化
  useEffect(() => {
    setAgentId(localStorage.getItem(AGENT_ID_KEY) ?? "");
  }, []);
  useEffect(() => {
    if (agentId) localStorage.setItem(AGENT_ID_KEY, agentId);
  }, [agentId]);

  // ── 转人工通知层（toast + 提示音 + 桌面通知 + 标题角标）──────
  const [toasts, setToasts] = useState<WaitingToast[]>([]);
  const [muted, setMuted] = useState(true);
  const [notifPerm, setNotifPerm] = useState<string>("default");
  const mutedRef = useRef(muted);
  mutedRef.current = muted;

  useEffect(() => {
    setMuted(localStorage.getItem(MUTE_KEY) === "1");
    if (typeof Notification !== "undefined") {
      setNotifPerm(Notification.permission);
    }
  }, []);

  const toggleMuted = () => {
    setMuted((m) => {
      localStorage.setItem(MUTE_KEY, m ? "0" : "1");
      return !m;
    });
  };

  const requestNotifPermission = async () => {
    if (typeof Notification === "undefined") return;
    try {
      setNotifPerm(await Notification.requestPermission());
    } catch {
      // 用户拒绝/浏览器不支持：保持现状
    }
  };

  const pushToast = useCallback((t: Omit<WaitingToast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev.slice(-3), { ...t, id }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((x) => x.id !== id));
    }, 10_000);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((x) => x.id !== id));
  }, []);

  // 新 waiting_human 工单触达：toast 必发，声音/桌面通知按开关与环境
  const notifyWaiting = useCallback(
    (item: HandoffQueueItem) => {
      const preview = item.last_message_preview ?? "（无消息）";
      const reason = item.trigger_reason ?? item.trigger_type ?? "用户转人工";
      pushToast({
        conversationId: item.conversation_id,
        preview,
        reason,
      });
      if (!mutedRef.current) playBeep();
      systemNotify("新转人工工单待接入", `${reason}：${preview}`);
    },
    [pushToast],
  );

  // 队列唯一写入口：diff 出新进入排队的工单 → 触发通知。
  // WS 与降级轮询两条通道都走这里，轮询路径也能弹通知。
  const queueRef = useRef<HandoffQueueItem[]>([]);
  const notifyWaitingRef = useRef(notifyWaiting);
  notifyWaitingRef.current = notifyWaiting;

  const applyQueue = useCallback((next: HandoffQueueItem[]) => {
    const prev = queueRef.current;
    const incoming = next.filter(
      (n) =>
        n.handoff_state === "waiting_human" &&
        !prev.some(
          (p) =>
            p.conversation_id === n.conversation_id &&
            p.handoff_state === "waiting_human",
        ),
    );
    queueRef.current = next;
    setQueue(next);
    for (const item of incoming) notifyWaitingRef.current(item);
  }, []);

  // 标题角标：有待接入工单时浏览器标签页直接可见（后台标签也能看到）
  useEffect(() => {
    const waiting = queue.filter((q) => q.handoff_state === "waiting_human").length;
    const base = "人工接入坐席工作台";
    document.title = waiting > 0 ? `(${waiting}) 待接入 — ${base}` : base;
  }, [queue]);

  // ── WS 事件处理（下行主通道）────────────────────────
  const handleAgentEvent = useCallback(
    (e: AgentEvent) => {
      switch (e.type) {
        case "conversation.waiting": {
          const item = e.item as HandoffQueueItem;
          const prev = queueRef.current;
          const idx = prev.findIndex(
            (q) => q.conversation_id === item.conversation_id,
          );
          const next =
            idx === -1
              ? [item, ...prev]
              : prev.map((q, i) => (i === idx ? item : q));
          applyQueue(next);
          break;
        }
        case "conversation.claimed": {
          const cid = e.conversation_id as string;
          applyQueue(
            queueRef.current.map((q) =>
              q.conversation_id === cid
                ? { ...q, handoff_state: "human_active" }
                : q,
            ),
          );
          if (selectedRef.current?.conversation_id === cid) {
            setSelected((s) =>
              s && s.conversation_id === cid
                ? { ...s, handoff_state: "human_active" }
                : s,
            );
          }
          break;
        }
        case "conversation.closed": {
          const cid = e.conversation_id as string;
          applyQueue(
            queueRef.current.filter((q) => q.conversation_id !== cid),
          );
          if (selectedRef.current?.conversation_id === cid) {
            setSelected((s) =>
              s && s.conversation_id === cid
                ? { ...s, handoff_state: "closed" }
                : s,
            );
          }
          break;
        }
        case "message.created": {
          const cid = e.conversation_id as string;
          const lastId = Number(e.last_id ?? 0);
          const msg = e.message as HandoffMessageDTO;
          if (selectedRef.current?.conversation_id !== cid) break;
          setMessages((prev) => {
            if (prev.some((m) => m.message_id === msg.message_id)) return prev;
            return [...prev, msg];
          });
          if (lastId > sinceIdRef.current) sinceIdRef.current = lastId;
          break;
        }
        default:
          // hello / heartbeat / pong：仅保活
          break;
      }
    },
    [applyQueue],
  );

  const { connected } = useAgentSocket(handleAgentEvent);

  // 全量对账：WS 刚连上（或重连）时拉一次队列 + 选中会话消息，
  // 补齐断线期间漏掉的事件（走 applyQueue：断线期间新排队的工单同样触发通知）
  const refreshQueue = useCallback(async () => {
    try {
      const res = await getHandoffQueue();
      applyQueue(res.items);
    } catch {
      // 静默，下轮重试
    }
  }, [applyQueue]);

  useEffect(() => {
    if (!connected) return;
    refreshQueue();
    const cid = selectedRef.current?.conversation_id;
    if (!cid) return;
    getHandoffMessages(cid, sinceIdRef.current)
      .then((res) => {
        if (res.messages.length > 0) {
          setMessages((prev) => {
            const known = new Set(prev.map((m) => m.message_id));
            return [...prev, ...res.messages.filter((m) => !known.has(m.message_id))];
          });
          sinceIdRef.current = res.last_id;
        }
      })
      .catch(() => undefined);
  }, [connected, refreshQueue]);

  // ── 降级轮询（仅 WS 未连接时）────────────────────────
  useEffect(() => {
    if (connected) return; // WS 主通道在线，轮询停止
    let alive = true;
    const tick = async () => {
      try {
        const res = await getHandoffQueue();
        if (alive) applyQueue(res.items);
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
  }, [connected, applyQueue]);

  // 选中会话：重置增量游标
  const selectConversation = useCallback((item: HandoffQueueItem) => {
    setSelected(item);
    setMessages([]);
    sinceIdRef.current = 0;
  }, []);

  // toast「查看」：定位到对应会话（队列里还在才可跳）
  const viewToastConversation = useCallback(
    (cid: string) => {
      const item = queueRef.current.find((q) => q.conversation_id === cid);
      if (item) selectConversation(item);
      setToasts((prev) => prev.filter((t) => t.conversationId !== cid));
    },
    [selectConversation],
  );

  // 消息增量轮询（降级：仅 WS 未连接且选中会话时，2s）
  useEffect(() => {
    if (!selected || connected) return;
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
  }, [selected, connected]);

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
      applyQueue(
        queueRef.current.map((q) =>
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

  const handleClose = async () => {
    if (!selected) return;
    if (!agentId.trim()) {
      setError("请先填写坐席 ID");
      return;
    }
    setError(null);
    try {
      await closeConversation(selected.conversation_id, agentId.trim());
      // 乐观更新（WS 在线时 conversation.closed 事件会再次兜底）
      applyQueue(
        queueRef.current.filter(
          (q) => q.conversation_id !== selected.conversation_id,
        ),
      );
      setSelected((s) =>
        s && s.conversation_id === selected.conversation_id
          ? { ...s, handoff_state: "closed" }
          : s,
      );
    } catch (e) {
      const msg = (e as Error).message ?? "关闭失败";
      setError(msg.includes("409") ? "会话状态不允许关闭" : msg);
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
      // WS 在线时 message.created 事件会实时追加；这里全量拉一次兜底
      // （同时拿到落库后的自增 id 作为增量游标）
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

  const closable =
    !!selected &&
    (selected.handoff_state === "waiting_human" ||
      selected.handoff_state === "human_active");

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Headphones size={20} className="text-blue-600" />
          <h1 className="text-lg font-medium">人工接入坐席工作台</h1>
          <span
            className={`px-2 py-0.5 rounded-full text-[10px] ${
              connected
                ? "bg-emerald-50 text-emerald-600"
                : "bg-amber-50 text-amber-600"
            }`}
            title={connected ? "WebSocket 实时推送在线" : "WS 离线，2s 轮询降级中"}
          >
            {connected ? "实时推送" : "轮询降级"}
          </span>
          <button
            onClick={toggleMuted}
            title={muted ? "提示音已静音，点击开启" : "提示音开启中，点击静音"}
            aria-label={muted ? "开启提示音" : "静音提示音"}
            className={`p-1.5 rounded-lg border transition-colors ${
              muted
                ? "border-slate-200 text-slate-400 hover:text-slate-600"
                : "border-blue-200 bg-blue-50 text-blue-600"
            }`}
          >
            {muted ? <VolumeX size={14} /> : <Volume2 size={14} />}
          </button>
          {typeof Notification !== "undefined" && notifPerm === "default" && (
            <button
              onClick={requestNotifPermission}
              title="授权浏览器桌面通知（页面在后台也能收到系统级提醒）"
              className="px-2 py-1 rounded-lg border border-slate-200 text-[11px]
                text-slate-500 hover:text-slate-700 hover:border-slate-300
                transition-colors flex items-center gap-1"
            >
              <BellRing size={12} />
              桌面通知
            </button>
          )}
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

      {/* 转人工通知 toast 堆栈 */}
      <div className="fixed top-4 right-4 z-50 w-80 space-y-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className="bg-white border border-red-200 rounded-xl shadow-lg p-3"
          >
            <div className="flex items-start gap-2">
              <BellRing size={15} className="text-red-500 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-800">
                  新转人工工单待接入
                </p>
                <p className="text-xs text-slate-500 truncate mt-0.5">
                  {t.reason}：{t.preview}
                </p>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <button
                  onClick={() => viewToastConversation(t.conversationId)}
                  className="px-2 py-1 text-xs rounded-lg bg-blue-600 text-white
                    hover:bg-blue-700 transition-colors"
                >
                  查看
                </button>
                <button
                  onClick={() => dismissToast(t.id)}
                  aria-label="关闭提醒"
                  className="p-1 text-slate-400 hover:text-slate-600"
                >
                  <XCircle size={14} />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

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
                <div className="flex items-center gap-2">
                  {stateBadge(selected.handoff_state)}
                  {closable && (
                    <button
                      onClick={handleClose}
                      className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg
                        border border-slate-300 text-slate-600 hover:bg-slate-100
                        transition-colors"
                      title="结束会话（waiting_human / human_active → closed）"
                    >
                      <XCircle size={13} />
                      结束会话
                    </button>
                  )}
                </div>
              </div>
              <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2.5">
                {messages.length === 0 && (
                  <div className="text-sm text-slate-400 text-center py-6">
                    暂无消息
                  </div>
                )}
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
