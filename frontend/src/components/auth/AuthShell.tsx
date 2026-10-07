"use client";

import type { CSSProperties, ReactNode } from "react";

/**
 * AuthShell — 登录/注册类页面的分屏骨架（统一门户视觉体系）。
 *
 * 右半区为毛玻璃表单卡，左半区为品牌主张；背景由主题化雾团（径向渐变 + 模糊）
 * 托住卡片。设计稿来自 Ardot「登录注册页-风格改版 / 统一门户-主页」，
 * 工程实现遵循现有 globals.css 变量（文本/边框），主题色通过 props 注入，不新增依赖。
 *
 * 左/右内容由页面组合，本组件只负责布局与背景，避免每个页面重复雾团与栅格。
 */
export interface AuthShellProps {
  /** 主色（按钮、高亮、胶囊标签底） */
  accent: string;
  /** 主色 hover */
  accentHover: string;
  /** 4 层径向渐变雾团（顺序：主色 / 顶部浅 / 右深 / 底部浅），用于右半区背景 */
  fog: [string, string, string, string];
  /** 顶部导航（可选；门户/登录共用同一导航形态） */
  nav?: ReactNode;
  /** 左侧品牌主张区 */
  left: ReactNode;
  /** 右侧毛玻璃表单卡内容 */
  children: ReactNode;
}

const BLOB_STYLE: CSSProperties[] = [
  { top: "-20%", right: "-14%", width: "60%", height: "72%" },
  { top: "-16%", right: "10%", width: "46%", height: "56%" },
  { bottom: "4%", right: "-8%", width: "54%", height: "66%" },
  { bottom: "-12%", left: "0%", width: "46%", height: "58%" },
];

export default function AuthShell({
  accent,
  accentHover,
  fog,
  nav,
  left,
  children,
}: AuthShellProps) {
  return (
    <main className="relative min-h-screen w-full overflow-hidden bg-white">
      {/* 雾团背景：仅右半区着色，左半区保持纯净白底以保证主张区可读性 */}
      {fog.map((bg, i) => (
        <div
          key={i}
          aria-hidden
          className="pointer-events-none absolute"
          style={{
            ...BLOB_STYLE[i],
            background: bg,
            filter: "blur(80px)",
            opacity: 0.9,
          }}
        />
      ))}

      {nav}

      <div className="relative z-10 flex min-h-screen flex-col lg:flex-row">
        {/* 左侧品牌主张区 */}
        <section className="flex w-full flex-col justify-center px-10 py-16 lg:w-[52%] lg:px-20">
          {left}
        </section>

        {/* 右侧表单卡 */}
        <section className="flex w-full items-center justify-center px-6 py-12 lg:w-[48%]">
          <div
            className="w-full max-w-[420px] rounded-3xl border border-white/70 bg-white/70 p-9 shadow-[0_18px_44px_-12px_rgba(15,26,20,0.12)] backdrop-blur-xl"
            style={{ WebkitBackdropFilter: "blur(20px)" }}
          >
            {children}
          </div>
        </section>
      </div>
    </main>
  );
}

/** 顶部导航（内部系统形态）：品牌 + 右侧链接 + 「内部系统」徽标 */
export function AuthNav({
  brand,
  links,
  badge = "内部系统",
}: {
  brand: string;
  links?: { label: string; href?: string }[];
  badge?: string;
}) {
  return (
    <nav className="absolute inset-x-0 top-0 z-20 flex h-[72px] items-center justify-between px-6 md:px-12">
      <div className="flex items-center gap-2.5">
        <span
          className="flex h-7 w-7 items-center justify-center rounded-[9px] text-[13px] font-bold text-white"
          style={{ background: "#16191A" }}
        >
          A
        </span>
        <span className="text-[17px] font-bold tracking-wide text-[#16191A]">
          {brand}
        </span>
      </div>
      <div className="flex items-center gap-3 md:gap-7">
        {links?.map((l) =>
          l.href ? (
            <a
              key={l.label}
              href={l.href}
              className="hidden text-[14px] text-[#5C6662] transition-colors hover:text-[#16191A] md:inline"
            >
              {l.label}
            </a>
          ) : (
            <span
              key={l.label}
              className="hidden text-[14px] text-[#5C6662] md:inline"
            >
              {l.label}
            </span>
          ),
        )}
        <span
          className="rounded-full px-3 py-1.5 text-[12px] font-medium"
          style={{ background: "rgba(31,122,77,0.10)", color: "#1F7A4D" }}
        >
          {badge}
        </span>
      </div>
    </nav>
  );
}
