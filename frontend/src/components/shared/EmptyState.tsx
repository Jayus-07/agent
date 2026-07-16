"use client";

interface Props {
  title?: string;
  description?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}

/**
 * 空数据态：表格无结果 / 列表为空 / 搜索无命中
 */
export default function EmptyState({
  title = "暂无数据",
  description,
  icon,
  action,
  className = "",
}: Props) {
  return (
    <div className={`flex flex-col items-center justify-center py-12 text-center ${className}`}>
      <div className="text-3xl text-slate-300 mb-2">
        {icon ?? "📭"}
      </div>
      <p className="text-sm font-medium text-slate-600">{title}</p>
      {description && <p className="text-xs text-slate-400 mt-1 max-w-md">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}