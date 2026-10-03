/**
 * travelTheme.ts — 旅游页页面级主题（2026-10-03 从散落的内联色值收口）
 *
 * 来源：参考 HTML #trip-concept 的 CSS 变量（青绿手账风）。这些色值**只在
 * 旅游页使用**，不是全局 design token——全局 accent 仍是蓝，改全局要走
 * 三端 globals.css + tailwind.config.ts 同步纪律（AGENTS.md）。
 *
 * 新增色值一律加在这里并注释用途；页面内联类名逐步向此收口，不做一次性
 * 全量替换（最小改动）。
 */
export const TP = {
  /** 主青绿：选中态/主按钮/链接 */
  accent: '#087b73',
  /** 主色深档：按钮 hover */
  accentDeep: '#06655f',
  /** 软底面板：条带底/只读区 */
  soft: '#f5faf9',
  /** 描边 */
  line: '#dae7e5',
  /** 墨色正文 */
  ink: '#183037',
  /** 次级文字 */
  muted: '#5c7074',
  /** 弱文字/占位 */
  faint: '#8fa5a3',
  /** 青绿浅底 chip */
  chipTealBg: '#e2f0ee',
  /** 美食橙浅底 chip */
  chipMealBg: '#fdf1e0',
  /** 美食橙文字 */
  chipMealText: '#b4690e',
  /** 在途/交通琥珀（负载条） */
  transit: '#f59e0b',
  /** 概览条地点图标底 */
  statVisitBg: '#e2f0ee',
  /** 概览条用餐图标底 */
  statMealBg: '#fdf1e0',
} as const
