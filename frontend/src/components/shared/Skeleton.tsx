"use client";

interface Props {
  rows?: number;
  cols?: number;
  className?: string;
}

/**
 * 表格 / 列表 loading 骨架屏
 */
export default function Skeleton({ rows = 5, cols = 4, className = "" }: Props) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-3">
          {Array.from({ length: cols }).map((__, j) => (
            <div
              key={j}
              className="flex-1 h-8 bg-slate-100 rounded animate-pulse"
              style={{ animationDelay: `${(i * cols + j) * 50}ms` }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}