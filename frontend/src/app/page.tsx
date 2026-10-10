/**
 * / — 统一门户主页
 *
 * 桌面端展示三种 AI 助手，以及客服端、管理端两个工作台入口；
 * 手机端只展示三种 AI 助手。助手入口登录后分别进入对应对话。
 */
import Link from "next/link";
import {
  ArrowRight,
  Building2,
  Headphones,
  MapPin,
  MessageCircle,
  Settings2,
  type LucideIcon,
} from "lucide-react";

const ADMIN_URL =
  process.env.NEXT_PUBLIC_ADMIN_URL?.trim() || "http://localhost:3200";
const CS_URL =
  process.env.NEXT_PUBLIC_CS_URL?.trim() || "http://localhost:3300";
const ADMIN_LOGIN_URL = `${ADMIN_URL.replace(/\/+$/, "")}/login?redirect=%2Fsettings%2Faccess`;
const CS_LOGIN_URL = `${CS_URL.replace(/\/+$/, "")}/login?redirect=%2Fcs`;

type AssistantEntry = {
  title: string;
  description: string;
  example: string;
  action: string;
  href: string;
  icon: LucideIcon;
  tone: "green" | "blue" | "orange";
};

const ASSISTANTS: AssistantEntry[] = [
  {
    title: "企业助手",
    description: "公司里的百事通。制度、流程、写材料，想问就问。",
    example: "报销流程又藏到哪儿了？",
    action: "找它聊聊",
    href: "/login?redirect=%2Fagent",
    icon: Building2,
    tone: "green",
  },
  {
    title: "智能客服",
    description: "客户的小问题，让 AI 先接住；需要时再转人工。",
    example: "我的订单到哪儿啦？",
    action: "问问客服",
    href: "/login?redirect=%2Fcustomer-service",
    icon: MessageCircle,
    tone: "blue",
  },
  {
    title: "旅游助手",
    description: "还没想好去哪？说说时间和心情，一起找灵感。",
    example: "周末想出去放松两天。",
    action: "聊聊去哪",
    href: "/login?redirect=%2Ftravel%2Fchat",
    icon: MapPin,
    tone: "orange",
  },
];

const toneStyles = {
  green: {
    icon: "bg-[#e8f3eb] text-[#287448]",
    dot: "bg-[#287448]",
    hover: "group-hover:border-[#a9cdb4]",
    action: "text-[#287448]",
  },
  blue: {
    icon: "bg-[#eaf1f8] text-[#356b98]",
    dot: "bg-[#356b98]",
    hover: "group-hover:border-[#b4cce0]",
    action: "text-[#356b98]",
  },
  orange: {
    icon: "bg-[#fbf0df] text-[#a56b21]",
    dot: "bg-[#a56b21]",
    hover: "group-hover:border-[#e5c99e]",
    action: "text-[#a56b21]",
  },
};

export default function PortalPage() {
  return (
    <main className="relative flex min-h-screen flex-col overflow-x-hidden overflow-y-auto bg-[#f7faf7] text-[#1d2822]">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-56 left-1/2 h-[430px] w-[760px] -translate-x-1/2 rounded-full bg-[#dcecdf] opacity-70 blur-[100px]"
      />

      <header className="relative z-10 mx-auto flex h-[88px] w-full max-w-[1240px] shrink-0 items-center justify-between px-5 sm:h-[96px] sm:px-8 lg:px-12">
        <Link href="/" className="relative top-3 flex items-center gap-3 rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#287448]">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#1d2822] text-sm font-bold text-white">
            A
          </span>
          <span className="text-[15px] font-semibold tracking-[0.01em] sm:text-base">
            智能助手平台
          </span>
        </Link>
        <nav aria-label="账号入口" className="relative top-3 flex items-center gap-2 sm:gap-3">
          <Link
            href="/login"
            className="rounded-full px-4 py-2 text-sm font-medium text-[#46534b] transition-colors hover:bg-white hover:text-[#1d2822] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#287448]"
          >
            登录
          </Link>
          <Link
            href="/register"
            className="rounded-full bg-[#287448] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[#1d6038] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#287448]"
          >
            注册
          </Link>
        </nav>
      </header>

      <div className="relative z-10 mx-auto flex w-full max-w-[1120px] flex-1 flex-col px-5 pb-10 sm:px-8 lg:px-10">
        <section className="pb-9 pt-12 text-center sm:pb-11 sm:pt-16">
          <p className="text-sm font-medium text-[#287448]">你的 AI 助手，都在这儿</p>
          <h1 className="mx-auto mt-4 max-w-[760px] text-[34px] font-semibold leading-[1.2] tracking-[-0.045em] text-[#1d2822] sm:text-[48px]">
            今天，想让哪位助手帮你？
          </h1>
          <p className="mx-auto mt-4 max-w-[520px] text-[15px] leading-7 text-[#69766e] sm:text-base">
            选一个，聊聊就有答案。
          </p>
        </section>

        <section aria-labelledby="assistants-heading">
          <div className="mb-4 flex items-end justify-between gap-4">
            <h2 id="assistants-heading" className="text-lg font-semibold text-[#26342b]">
              AI 助手
            </h2>
            <p className="hidden text-sm text-[#87928b] sm:block">遇到不同的事，就找不同的搭档</p>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3">
            {ASSISTANTS.map((assistant) => {
              const Icon = assistant.icon;
              const tone = toneStyles[assistant.tone];

              return (
                <Link
                  key={assistant.title}
                  href={assistant.href}
                  className={`group flex min-h-[270px] flex-col rounded-[20px] border border-[#e2e9e3] bg-white p-5 shadow-[0_5px_18px_rgba(26,50,34,0.035)] transition-colors ${tone.hover} focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#287448] sm:p-6`}
                >
                  <div className="flex items-center justify-between">
                    <span className={`flex h-11 w-11 items-center justify-center rounded-[14px] ${tone.icon}`}>
                      <Icon size={21} strokeWidth={1.8} aria-hidden="true" />
                    </span>
                    <span className={`h-2 w-2 rounded-full ${tone.dot}`} aria-hidden="true" />
                  </div>
                  <h3 className="mt-5 text-[19px] font-semibold tracking-[-0.02em] text-[#1d2822]">
                    {assistant.title}
                  </h3>
                  <p className="mt-2 min-h-[48px] text-sm leading-6 text-[#68756d]">
                    {assistant.description}
                  </p>
                  <p className="mt-4 rounded-xl bg-[#f6f8f6] px-3.5 py-3 text-[13px] leading-5 text-[#536158]">
                    “{assistant.example}”
                  </p>
                  <span className={`mt-auto flex items-center gap-2 pt-5 text-sm font-semibold ${tone.action}`}>
                    {assistant.action}
                    <ArrowRight size={16} strokeWidth={1.8} aria-hidden="true" />
                  </span>
                </Link>
              );
            })}
          </div>
        </section>

        <section aria-labelledby="workbenches-heading" className="mt-10 hidden md:block">
          <div className="mb-4 flex items-end justify-between gap-4 border-b border-[#e3e9e4] pb-3">
            <h2 id="workbenches-heading" className="text-lg font-semibold text-[#26342b]">
              工作台
            </h2>
            <p className="text-sm text-[#87928b]">客服与平台管理入口</p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <a
              href={CS_LOGIN_URL}
              className="group flex items-center gap-4 rounded-2xl border border-[#e2e9e3] bg-white px-5 py-4 transition-colors hover:border-[#b9cbbd] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#287448]"
            >
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] bg-[#edf3f8] text-[#356b98]">
                <Headphones size={21} strokeWidth={1.8} aria-hidden="true" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[15px] font-semibold text-[#26342b]">客服端</span>
                <span className="mt-1 block text-sm text-[#748078]">真人客服的工作台，接待会话、跟进客户问题</span>
              </span>
              <ArrowRight size={17} className="shrink-0 text-[#89948d] transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
            </a>
            <a
              href={ADMIN_LOGIN_URL}
              className="group flex items-center gap-4 rounded-2xl border border-[#e2e9e3] bg-white px-5 py-4 transition-colors hover:border-[#b9cbbd] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#287448]"
            >
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] bg-[#f0f0ee] text-[#535b54]">
                <Settings2 size={21} strokeWidth={1.8} aria-hidden="true" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[15px] font-semibold text-[#26342b]">管理端</span>
                <span className="mt-1 block text-sm text-[#748078]">管理平台配置，为已注册用户分配角色</span>
              </span>
              <ArrowRight size={17} className="shrink-0 text-[#89948d] transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
            </a>
          </div>
        </section>
      </div>

      <footer className="relative z-10 border-t border-[#e7ece8] bg-white/70">
        <div className="mx-auto flex min-h-[58px] max-w-[1240px] flex-col justify-center gap-1 px-5 py-3 text-xs text-[#849087] sm:flex-row sm:items-center sm:justify-between sm:px-8 lg:px-12">
          <span>智能助手平台 · 让每个问题，都有合适的搭档</span>
          <span>© 2026 公司名称</span>
        </div>
      </footer>
    </main>
  );
}
