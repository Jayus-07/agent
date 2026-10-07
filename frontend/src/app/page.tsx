/**
 * / — 统一门户主页（设计稿 05 · 内部使用）
 *
 * 面向公司内部的统一入口：一屏内提供用户端 / 客服端 / 管理端三个工作台的导航卡，
 * 员工无需分别记忆入口。
 * - 用户端：本应用内（/login 登录后进入 /agent）
 * - 客服端：独立应用 frontend-cs（:3300），此处用绝对链接跨应用跳转
 * - 管理端：独立应用 frontend-admin（:3200），此处用绝对链接跨应用跳转
 *
 * 纯静态呈现，无登录态要求；背景雾团与三端登录页视觉同源，进入对应端后色调延续。
 *
 * 移动端（≤md）口径（2026-10-07 用户拍板）：三端只有用户端适合手机展示，
 * 门户页在窄屏直接让位给登录页——手机开首页看到三张卡再点用户端等于白绕一屏。
 * 故 ≤md 走 MobileRedirect 重定向 /login，桌面保持三卡门户。
 * 客服端/管理端在手机端不提供入口（客服外出用手机接单的诉求已由用户确认放弃）。
 */
import Link from "next/link";
import MobileRedirect from "@/components/layout/MobileRedirect";

const ADMIN_URL =
  process.env.NEXT_PUBLIC_ADMIN_URL && process.env.NEXT_PUBLIC_ADMIN_URL.trim()
    ? process.env.NEXT_PUBLIC_ADMIN_URL.trim()
    : "http://localhost:3200";

const CS_URL =
  process.env.NEXT_PUBLIC_CS_URL && process.env.NEXT_PUBLIC_CS_URL.trim()
    ? process.env.NEXT_PUBLIC_CS_URL.trim()
    : "http://localhost:3300";

type Entry = {
  key: string;
  tag: string;
  tagColor: string;
  tagBg: string;
  title: string;
  desc: string;
  accent: string;
  href: string;
  external?: boolean;
  icon: React.ReactNode;
};

const ENTRIES: Entry[] = [
  {
    key: "user",
    tag: "用户端",
    tagColor: "#1F7A4D",
    tagBg: "rgba(31,122,77,0.10)",
    title: "AI 问答 · 多模态客服",
    desc: "知识库问答、图片与文档理解、订单与售后查询，一句话全部接住；答不了的问题自动转人工。",
    accent: "#1F7A4D",
    href: "/login",
    icon: (
      <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
        <path
          d="M5 8.5C5 7.1 6.1 6 7.5 6H20.5C21.9 6 23 7.1 23 8.5V17.5C23 18.9 21.9 20 20.5 20H11L6 24V20H7.5C6.1 20 5 18.9 5 17.5V8.5Z"
          fill="#1F7A4D"
        />
        <circle cx="11" cy="13" r="1.4" fill="#fff" />
        <circle cx="14" cy="13" r="1.4" fill="#fff" />
        <circle cx="17" cy="13" r="1.4" fill="#fff" />
      </svg>
    ),
  },
  {
    key: "cs",
    tag: "客服端",
    tagColor: "#2C6E9E",
    tagBg: "rgba(44,110,158,0.10)",
    title: "客服工作台",
    desc: "AI 优先应答，答不了自动转人工；会话、工单与知识库在同一屏流转，转接与质检全程留痕。",
    accent: "#2C6E9E",
    href: CS_URL,
    external: true,
    icon: (
      <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
        <path
          d="M6 14V12C6 8.7 8.7 6 12 6H16C19.3 6 22 8.7 22 12V14"
          stroke="#2C6E9E"
          strokeWidth="2.2"
          strokeLinecap="round"
        />
        <rect x="4.5" y="13" width="4" height="7" rx="2" fill="#2C6E9E" />
        <rect x="19.5" y="13" width="4" height="7" rx="2" fill="#2C6E9E" />
        <path d="M22 19V20C22 22.2 20.2 24 18 24H15" stroke="#2C6E9E" strokeWidth="2.2" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    key: "admin",
    tag: "管理端",
    tagColor: "#2E333A",
    tagBg: "rgba(46,51,58,0.10)",
    title: "管理控制台",
    desc: "模型与技能编排、权限与租户、审计与配额，全部集中管理；所有变更可追溯、可回滚。",
    accent: "#2E333A",
    href: ADMIN_URL,
    external: true,
    icon: (
      <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
        <rect x="5" y="5" width="8" height="8" rx="2" fill="#2E333A" />
        <rect x="15" y="5" width="8" height="5" rx="2" fill="#2E333A" />
        <rect x="15" y="12" width="8" height="11" rx="2" fill="#2E333A" />
        <rect x="5" y="15" width="8" height="8" rx="2" fill="#2E333A" />
      </svg>
    ),
  },
];

export default function PortalPage() {
  return (
    <main className="relative min-h-screen w-full overflow-hidden bg-white">
      {/* 移动端让位登录页；桌面端本组件渲染 null，门户三卡照常 */}
      <MobileRedirect to="/login" />
      {/* 绿调雾团背景（与用户端登录同源） */}
      <div
        aria-hidden
        className="pointer-events-none absolute"
        style={{
          top: "-22%",
          right: "8%",
          width: "60%",
          height: "70%",
          background:
            "radial-gradient(circle at 30% 30%, rgba(80,160,110,0.40), transparent 70%)",
          filter: "blur(110px)",
          opacity: 0.9,
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute"
        style={{
          bottom: "-10%",
          left: "2%",
          width: "46%",
          height: "58%",
          background:
            "radial-gradient(circle at 50% 50%, rgba(200,225,205,0.55), transparent 70%)",
          filter: "blur(90px)",
          opacity: 0.9,
        }}
      />

      {/* 顶部导航（≤md 收起文字链接，避免窄屏挤压竖排） */}
      <nav className="relative z-20 flex h-[72px] items-center justify-between px-6 md:px-12">
        <div className="flex items-center gap-2.5">
          <span
            className="flex h-7 w-7 items-center justify-center rounded-[9px] text-[13px] font-bold text-white"
            style={{ background: "#16191A" }}
          >
            A
          </span>
          <span className="text-[17px] font-bold tracking-wide text-[#16191A]">
            智能协作平台
          </span>
        </div>
        <div className="flex items-center gap-3 md:gap-7">
          <span className="hidden text-[14px] text-[#5C6662] md:inline">工作台</span>
          <span className="hidden text-[14px] text-[#5C6662] md:inline">帮助中心</span>
          <span className="hidden text-[14px] text-[#5C6662] md:inline">使用文档</span>
          <span
            className="rounded-full px-3 py-1.5 text-[12px] font-medium"
            style={{ background: "rgba(31,122,77,0.10)", color: "#1F7A4D" }}
          >
            内部系统
          </span>
        </div>
      </nav>

      {/* 主视觉 */}
      <section className="relative z-10 mx-auto flex max-w-[920px] flex-col items-center px-6 pt-10 pb-8 text-center md:pt-16">
        <span className="inline-flex items-center rounded-full border border-black/10 bg-white/70 px-4 py-1.5 text-[13px] text-[#4A544F]">
          统一入口 · 一个平台，三个工作台
        </span>
        <h1 className="mt-6 text-[34px] font-bold leading-[44px] tracking-[-1px] text-[#16191A] md:text-[50px] md:leading-[62px]">
          选择一个工作台，开始你的工作
        </h1>
        <p className="mt-5 max-w-[660px] text-[17px] leading-7 text-[#6E7873]">
          本门户整合 AI 问答、客服工作台与管理控制台，按需进入对应系统，无需分别记忆入口。
        </p>
        <p className="mt-3 text-[13px] text-[#98A29D]">
          已为 3 个业务系统提供统一单点入口
        </p>
      </section>

      {/* 入口卡 */}
      <section className="relative z-10 mx-auto flex max-w-[1200px] flex-col items-stretch justify-center gap-7 px-6 pb-16 sm:flex-row">
        {ENTRIES.map((e) => {
          const Inner = (
            <div className="flex w-full max-w-[360px] flex-col rounded-2xl border border-[#E3E8E4] bg-white p-7 shadow-[0_12px_28px_rgba(15,26,20,0.08)] transition-transform hover:-translate-y-1">
              <div
                className="flex h-14 w-14 items-center justify-center rounded-2xl"
                style={{ background: e.tagBg }}
              >
                {e.icon}
              </div>
              <span
                className="mt-5 inline-flex w-fit items-center rounded-full px-2.5 py-1 text-[12px] font-medium"
                style={{ background: e.tagBg, color: e.tagColor }}
              >
                {e.tag}
              </span>
              <h3 className="mt-3 text-[20px] font-semibold text-[#16191A]">
                {e.title}
              </h3>
              <p className="mt-2 flex-1 text-[14px] leading-[22px] text-[#6E7873]">
                {e.desc}
              </p>
              <span
                className="mt-5 inline-flex items-center justify-center gap-2 rounded-xl py-3 text-[15px] font-medium text-white"
                style={{ background: e.accent }}
              >
                进入{e.tag === "用户端" ? "工作台" : e.tag === "客服端" ? "工作台" : "控制台"}
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M2.6 8H13.2" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />
                  <path
                    d="M8.8 3.6L13.2 8L8.8 12.4"
                    stroke="#fff"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </span>
            </div>
          );
          return e.external ? (
            <a key={e.key} href={e.href} className="flex justify-center">
              {Inner}
            </a>
          ) : (
            <Link key={e.key} href={e.href} className="flex justify-center">
              {Inner}
            </Link>
          );
        })}
      </section>

      {/* 页脚（移动端纵排换行） */}
      <footer className="relative z-10 flex min-h-16 flex-col items-start justify-center gap-1.5 border-t border-[#EEF1EF] bg-[#F7F9F8] px-6 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-0 md:px-12">
        <div className="flex items-center gap-2 text-[13px] text-[#7A8480]">
          <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
            <rect x="3.2" y="6.5" width="8.6" height="6.3" rx="1.4" stroke="#7A8480" strokeWidth="1.2" />
            <path d="M5 6.5V4.6C5 3.2 6.1 2.2 7.5 2.2C8.9 2.2 10 3.2 10 4.6V6.5" stroke="#7A8480" strokeWidth="1.2" />
            <circle cx="7.5" cy="9.4" r="1" fill="#7A8480" />
          </svg>
          内部系统 · 仅限授权人员访问 · 所有访问与操作均记录审计日志
        </div>
        <span className="text-[13px] text-[#98A29D]">© 2026 公司名称</span>
      </footer>
    </main>
  );
}
