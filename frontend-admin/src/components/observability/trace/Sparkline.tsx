"use client";

/**
 * 极简 SVG 折线图（无依赖）
 * 用于指标卡的趋势展示
 */

interface Props {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  fillColor?: string;
  showLast?: boolean;
}

export default function Sparkline({
  data,
  width = 80,
  height = 24,
  color = "#10b981",
  fillColor,
  showLast = true,
}: Props) {
  if (!data || data.length === 0) return null;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const padding = 2;
  const w = width - padding * 2;
  const h = height - padding * 2;

  const points = data.map((v, i) => {
    const x = padding + (i / (data.length - 1 || 1)) * w;
    const y = padding + h - ((v - min) / range) * h;
    return [x, y];
  });

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p[0]} ${p[1]}`).join(" ");
  const areaPath = `${path} L ${points[points.length - 1][0]} ${padding + h} L ${points[0][0]} ${padding + h} Z`;

  const last = points[points.length - 1];

  return (
    <svg width={width} height={height} className="block">
      {fillColor && <path d={areaPath} fill={fillColor} opacity={0.2} />}
      <path d={path} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      {showLast && <circle cx={last[0]} cy={last[1]} r={2.5} fill={color} />}
    </svg>
  );
}