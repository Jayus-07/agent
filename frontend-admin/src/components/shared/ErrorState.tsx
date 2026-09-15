"use client";

interface Props {
  title?: string;
  message?: string;
  code?: string | number;
  onRetry?: () => void;
  className?: string;
}

/**
 * 错误态：API 失败 / 加载异常 / 404 等
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
      <div className="text-3xl mb-2">⚠️</div>
      <p className="text-sm font-medium text-red-600">{title}</p>
      {code !== undefined && (
        <p className="text-[10px] font-mono text-slate-400 mt-1">错误代码 {code}</p>
      )}
      <p className="text-xs text-slate-500 mt-2 max-w-md">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-4 text-xs text-white bg-violet-600 hover:bg-violet-700 rounded-lg px-3 py-1.5"
        >
          重试
        </button>
      )}
    </div>
  );
}