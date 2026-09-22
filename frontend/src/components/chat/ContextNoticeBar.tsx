"use client";

import { useChatStore } from "@/store/chat";

/**
 * ContextNoticeBar — 上下文预算提示条（2026-09-22 Context Budget 基础版）
 *
 * - 左侧：context SSE 事件累计的「已优化上下文 · 节省 N tokens」灰色系统提示条；
 * - 右侧：done.context_usage 的「上下文 xx%」。
 * 仅 UI runtime 展示：不入聊天历史、不落库（后端不写 chat_messages，
 * 前端 resetStream 每轮清零）。
 */
export default function ContextNoticeBar() {
  const saved = useChatStore((s) => s.contextSavedTokens);
  const usage = useChatStore((s) => s.contextUsage);
  if (saved <= 0 && !usage) return null;

  const percent = usage ? Math.round(usage.usage_ratio * 100) : null;

  return (
    <div
      className="mx-5 mt-2 flex items-center gap-2 text-xs"
      role="status"
      aria-label="上下文状态"
    >
      {saved > 0 && (
        <span className="rounded-full bg-gray-100 px-2.5 py-1 text-gray-500">
          已优化上下文 · 节省 {saved} tokens
        </span>
      )}
      {percent !== null && (
        <span className="ml-auto tabular-nums text-gray-400">
          上下文 {percent}%
        </span>
      )}
    </div>
  );
}
