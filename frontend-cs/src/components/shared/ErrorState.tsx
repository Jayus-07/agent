"use client";

import { AlertTriangle, RotateCw } from "lucide-react";

interface Props {
  title?: string;
  message?: string;
  code?: string | number;
  onRetry?: () => void;
  className?: string;
}

/**
 * 错误态：API 失败 / 加载异常 / 404 等（对齐「AI Native」设计系统：
 * 主色 #4D6BFE、lucide 图标、语义化文字色，替代散落各页的手写裸错误文案）。
 */
export default function ErrorState({
  title = "加载失败",
  message = "请稍后重试或联系管理员",
  code,
  onRetry,
  className = "",
}: Props) {
  return (
    <div className={`flex flex-col items-center justify-center py-12 text-center ${className}`}>
      <div className="w-12 h-12 rounded-xl bg-red-50 flex items-center justify-center mb-3">
        <AlertTriangle size={22} className="text-red-500" strokeWidth={1.75} />
      </div>
      <p className="text-sm font-medium text-red-600">{title}</p>
      {code !== undefined && (
        <p className="text-[10px] font-mono text-text-muted mt-1">错误代码 {code}</p>
      )}
      <p className="text-xs text-text-secondary mt-2 max-w-md leading-relaxed">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-4 inline-flex items-center gap-1.5 text-xs text-white bg-accent
            hover:bg-accent-hover rounded-lg px-3.5 py-1.5 shadow-sm
            active:scale-[0.98] transition-all"
        >
          <RotateCw size={12} />
          重试
        </button>
      )}
    </div>
  );
}
