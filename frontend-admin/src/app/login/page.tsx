"use client";

/**
 * /login — 管理端登录页（统一门户视觉体系 · 石墨主题）
 *
 * 复用管理端既有登录态逻辑（lib/auth）：登录、记住账号、
 * 会话过期标记。视觉对齐设计稿 04：分屏 + 毛玻璃卡 + 石墨色雾团，与门户/三端同源。
 * 管理端账号由管理员统一开通，无注册入口。
 */
import { Suspense, useEffect, useState, type FormEvent, type InputHTMLAttributes } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  clearSavedUsername,
  consumeExpiredFlag,
  getSavedUsername,
  login,
  saveUsername,
} from "@/lib/auth";

const ACCENT = "#2E333A";
const ACCENT_HOVER = "#1f2329";
const FOG: [string, string, string, string] = [
  "radial-gradient(circle at 30% 30%, rgba(60,66,76,0.50), transparent 70%)",
  "radial-gradient(circle at 72% 18%, rgba(110,116,126,0.40), transparent 70%)",
  "radial-gradient(circle at 82% 82%, rgba(30,34,40,0.50), transparent 70%)",
  "radial-gradient(circle at 8% 92%, rgba(190,194,200,0.55), transparent 70%)",
];

function AuthInput({
  label,
  accent,
  ...rest
}: { label: string; accent: string } & InputHTMLAttributes<HTMLInputElement>) {
  const [focused, setFocused] = useState(false);
  return (
    <div>
      <label className="mb-1.5 block text-[13px] font-medium text-[#3F4A46]">{label}</label>
      <input
        {...rest}
        onFocus={(e) => {
          setFocused(true);
          rest.onFocus?.(e);
        }}
        onBlur={(e) => {
          setFocused(false);
          rest.onBlur?.(e);
        }}
        className="h-12 w-full rounded-xl border bg-white/90 px-3.5 text-[14px] text-[#16191A] outline-none placeholder:text-[#A3ADA8]"
        style={{
          borderColor: focused ? accent : "#E3E8E4",
          boxShadow: focused ? `0 0 0 3px ${accent}1f` : "none",
        }}
      />
    </div>
  );
}

function AdminLoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirect = searchParams.get("redirect") || "/";

  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const saved = getSavedUsername();
    if (saved) {
      setAccount(saved);
      setRemember(true);
    }
    if (consumeExpiredFlag()) setNotice("登录已过期，请重新登录");
  }, []);

  const goNext = () => {
    router.replace(redirect.startsWith("/") ? redirect : "/");
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (loading) return;
    setError("");
    setNotice("");
    if (!account.trim() || !password) {
      setError("请输入账号和密码");
      return;
    }
    setLoading(true);
    try {
      await login(account.trim(), password);
      // 2026-09-23 安全收口：只记账号，密码永不落 localStorage（XSS 可还原）
      if (remember) saveUsername(account.trim());
      else clearSavedUsername();
      goNext();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请稍后重试");
      setLoading(false);
    }
  };

  return (
    <main className="relative min-h-screen w-full overflow-hidden bg-white">
      {FOG.map((bg, i) => {
        const pos = [
          { top: "-20%", right: "-14%", width: "60%", height: "72%" },
          { top: "-16%", right: "10%", width: "46%", height: "56%" },
          { bottom: "4%", right: "-8%", width: "54%", height: "66%" },
          { bottom: "-12%", left: "0%", width: "46%", height: "58%" },
        ][i];
        return (
          <div
            key={i}
            aria-hidden
            className="pointer-events-none absolute"
            style={{ ...pos, background: bg, filter: "blur(80px)", opacity: 0.9 }}
          />
        );
      })}

      {/* 导航 */}
      <nav className="absolute inset-x-0 top-0 z-20 flex h-[72px] items-center justify-between px-12">
        <div className="flex items-center gap-2.5">
          <span
            className="flex h-7 w-7 items-center justify-center rounded-[9px] text-[13px] font-bold text-white"
            style={{ background: "#16191A" }}
          >
            A
          </span>
          <span className="text-[17px] font-bold tracking-wide text-[#16191A]">
            智能助手平台 · 管理
          </span>
        </div>
        <div className="flex items-center gap-7">
          <span className="text-[14px] text-[#5C6662]">返回官网</span>
          <span className="text-[14px] text-[#5C6662]">帮助中心</span>
          <span
            className="rounded-full px-3 py-1.5 text-[12px] font-medium"
            style={{ background: "rgba(46,51,58,0.10)", color: "#2E333A" }}
          >
            内部系统
          </span>
        </div>
      </nav>

      <div className="relative z-10 flex min-h-screen flex-col lg:flex-row">
        {/* 左侧主张 */}
        <section className="flex w-full flex-col justify-center px-10 py-16 lg:w-[52%] lg:px-20">
          <div className="max-w-[520px]">
            <span className="inline-flex items-center rounded-full border border-black/10 bg-white/70 px-3.5 py-1.5 text-[13px] text-[#4A544F]">
              管理控制台
            </span>
            <h1 className="mt-7 whitespace-pre-line text-[56px] font-bold leading-[68px] tracking-[-1.5px] text-[#16191A]">
              {"全局配置，\n一处掌控"}
            </h1>
            <p className="mt-6 max-w-[470px] text-[16px] leading-7 text-[#6E7873]">
              模型与技能编排、权限与租户、审计与配额，全部集中管理；所有变更可追溯、可回滚。
            </p>
            <p className="mt-8 inline-flex items-center gap-2 text-[14px] text-[#7A8480]">
              <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
                <rect x="3.2" y="6.5" width="8.6" height="6.3" rx="1.4" stroke="#7A8480" strokeWidth="1.2" />
                <path d="M5 6.5V4.6C5 3.2 6.1 2.2 7.5 2.2C8.9 2.2 10 3.2 10 4.6V6.5" stroke="#7A8480" strokeWidth="1.2" />
                <circle cx="7.5" cy="9.4" r="1" fill="#7A8480" />
              </svg>
              所有操作审计留痕 · 支持私有化部署
            </p>
          </div>
        </section>

        {/* 右侧表单卡 */}
        <section className="flex w-full items-center justify-center px-6 py-12 lg:w-[48%]">
          <div
            className="w-full max-w-[420px] rounded-3xl border border-white/70 bg-white/70 p-9 shadow-[0_18px_44px_-12px_rgba(15,26,20,0.12)] backdrop-blur-xl"
            style={{ WebkitBackdropFilter: "blur(20px)" }}
          >
            <div className="mb-6">
              <h2 className="text-[26px] font-semibold text-[#16191A]">管理员登录</h2>
              <p className="mt-1.5 text-[13px] text-[#7A8480]">请使用管理员账号登录控制台</p>
            </div>

            {notice && (
              <div className="mb-4 rounded-lg bg-[#FAEEDA] px-3 py-2.5 text-[12px] text-[#633806]">
                {notice}
              </div>
            )}
            {error && (
              <div className="mb-4 rounded-lg bg-[#FCEBEB] px-3 py-2.5 text-[12px] text-[#791F1F]">
                {error}
              </div>
            )}

            <form onSubmit={handleSubmit} noValidate>
              <div className="space-y-4">
                <AuthInput
                  label="管理员手机号 / 邮箱"
                  accent={ACCENT}
                  type="text"
                  autoComplete="username"
                  placeholder="请输入管理员手机号或邮箱"
                  value={account}
                  onChange={(e) => setAccount(e.target.value)}
                />
                <AuthInput
                  label="登录密码"
                  accent={ACCENT}
                  type="password"
                  autoComplete="current-password"
                  placeholder="请输入登录密码"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>

              <label className="mt-4 flex cursor-pointer items-center gap-2 text-[13px] text-[#5C6662]">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                  style={{ accentColor: ACCENT }}
                />
                记住账号（本机保存，下次自动填充账号）
              </label>

              <button
                type="submit"
                disabled={loading}
                className="mt-6 w-full rounded-xl py-3.5 text-[16px] font-medium text-white transition-colors disabled:cursor-not-allowed disabled:opacity-60"
                style={{ background: ACCENT }}
                onMouseEnter={(e) => {
                  if (!loading) e.currentTarget.style.background = ACCENT_HOVER;
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = ACCENT;
                }}
              >
                {loading ? "处理中…" : "进入控制台"}
              </button>

              <p className="mt-5 text-center text-[12px] text-[#98A29D]">
                账号由管理员统一开通
              </p>
            </form>
          </div>
        </section>
      </div>
    </main>
  );
}

export default function AdminLoginPage() {
  return (
    <Suspense fallback={null}>
      <AdminLoginForm />
    </Suspense>
  );
}
