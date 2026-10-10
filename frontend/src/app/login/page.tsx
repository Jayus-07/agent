"use client";

/**
 * /login — 用户端登录页（统一门户视觉体系 · 分屏 + 毛玻璃卡）
 *
 * - 绿色主题，复用既有登录态逻辑：redirect 回跳、默认记住账号密码、
 *   会话过期标记（consumeExpiredFlag）。第三方登录为占位（禁用·敬请期待）。
 * - 客服工作台登录由独立应用 frontend-cs（:3300）处理；用户选择智能客服助手时，
 *   仍复用此登录页，并在登录后跳转到独立的用户客服对话页。
 * - 注册入口移到独立 /register 页（见设计稿 02）。
 */
import { Suspense, useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  clearSavedUsername,
  consumeExpiredFlag,
  consumePendingLoginCredentials,
  getSavedCredentials,
  getSavedUsername,
  login,
  saveCredentials,
  saveUsername,
} from "@/lib/auth";
import AuthShell, { AuthNav } from "@/components/auth/AuthShell";
import AuthInput from "@/components/auth/AuthInput";

const THEME = {
  accent: "#1F7A4D",
  accentHover: "#17633d",
  fog: [
    "radial-gradient(circle at 30% 30%, rgba(80,160,110,0.55), transparent 70%)",
    "radial-gradient(circle at 72% 18%, rgba(120,195,150,0.45), transparent 70%)",
    "radial-gradient(circle at 82% 82%, rgba(50,110,75,0.5), transparent 70%)",
    "radial-gradient(circle at 8% 92%, rgba(200,225,205,0.6), transparent 70%)",
  ] as [string, string, string, string],
};

// 仓库提供的三组演示订单账号；普通新账号仍使用 11 位数字登录。
const DEMO_LOGIN_USERNAMES = new Set(["demo_fuzhou", "demo_li", "demo_wang"]);

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirect = searchParams.get("redirect") || "/agent";
  const selectedAssistant = redirect.startsWith("/customer-service")
    ? "智能客服"
    : redirect.startsWith("/travel")
      ? "旅游助手"
      : "企业助手";
  const registerHref = `/register?redirect=${encodeURIComponent(redirect)}`;
  const registrationComplete = searchParams.get("registered") === "1";
  const theme = THEME;

  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const credentialsHydrated = useRef(false);
  const isDemoAccount = DEMO_LOGIN_USERNAMES.has(account.trim());
  const isNumericAccount = /^\d*$/.test(account.trim());
  useEffect(() => {
    if (credentialsHydrated.current) return;
    credentialsHydrated.current = true;
    const pendingCredentials = consumePendingLoginCredentials();
    const savedCredentials = getSavedCredentials();
    const saved = getSavedUsername();
    const username = pendingCredentials?.username || savedCredentials?.username || saved;
    const savedPassword = pendingCredentials?.password || savedCredentials?.password;
    if (username) {
      setAccount(username);
      setRemember(true);
    }
    if (savedPassword) setPassword(savedPassword);
    if (consumeExpiredFlag()) {
      setNotice("登录已过期，请重新登录");
    } else if (registrationComplete) {
      setNotice("注册成功，请核对账号密码后登录");
    }
  }, [registrationComplete]);

  const goNext = () => {
    router.replace(redirect.startsWith("/") ? redirect : "/agent");
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (loading) return;
    setError("");
    setNotice("");
    if (!DEMO_LOGIN_USERNAMES.has(account.trim()) && !/^\d{11}$/.test(account.trim())) {
      setError("请输入 11 位数字手机号");
      return;
    }
    if (!password) {
      setError("请输入登录密码");
      return;
    }
    setLoading(true);
    try {
      const result = await login(account.trim(), password);
      // 临时密码首次登录（P6.3/TD-04）：先改密，改完由改密页进 /agent
      if (result.mustChangePassword) {
        if (remember) saveUsername(account.trim());
        else clearSavedUsername();
        router.replace("/change-password");
        return;
      }
      if (remember) saveCredentials(account.trim(), password);
      else clearSavedUsername();
      goNext();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请稍后重试");
      setLoading(false);
    }
  };

  const left = (
    <div className="max-w-[520px]">
      <span
        className="inline-flex items-center rounded-full border border-black/10 bg-white/70 px-3.5 py-1.5 text-[13px] text-[#4A544F]"
      >
        即将进入：{selectedAssistant}
      </span>
      <h1
        className="mt-7 whitespace-pre-line text-[56px] font-bold leading-[68px] tracking-[-1.5px] text-[#16191A]"
      >
        {"回来继续，\n助手已经就位"}
      </h1>
      <p className="mt-6 max-w-[470px] text-[16px] leading-7 text-[#6E7873]">
        登录后直达{selectedAssistant}，接着处理眼前的事。
      </p>
      <Link
        href={registerHref}
        className="mt-8 inline-flex items-center gap-2 rounded-full bg-[#16191A] px-6 py-3.5 text-[15px] font-medium text-white transition-opacity hover:opacity-90"
      >
        还没有账号？免费注册
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
      </Link>
    </div>
  );

  return (
    <AuthShell
      accent={theme.accent}
      accentHover={theme.accentHover}
      fog={theme.fog}
      nav={
        <AuthNav
          brand={"智能助手平台"}
          links={[{ label: "返回门户", href: "/" }]}
          badge="统一入口"
        />
      }
      left={left}
    >
      <div className="mb-5 lg:mb-6">
        <span className="mb-3 inline-flex items-center rounded-full border border-black/10 bg-white/70 px-3 py-1 text-[12px] text-[#4A544F] lg:hidden">
          即将进入：{selectedAssistant}
        </span>
        <h2 className="text-[22px] font-semibold text-[#16191A] sm:text-[24px] lg:text-[26px]">
          登录你的账号
        </h2>
        <p className="mt-1.5 text-[13px] text-[#7A8480]">
          使用注册时的 11 位数字账号登录，直达{selectedAssistant}
        </p>
      </div>

      {notice && (
        <div
          className="mb-4 rounded-lg bg-[#FAEEDA] px-3 py-2.5 text-[12px] text-[#633806]"
        >
          {notice}
        </div>
      )}
      {error && (
        <div
          className="mb-4 rounded-lg bg-[#FCEBEB] px-3 py-2.5 text-[12px] text-[#791F1F]"
        >
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit} noValidate>
        <div className="space-y-4">
          <AuthInput
            label={isDemoAccount ? "演示账号" : "11 位数字账号"}
            accent={theme.accent}
            type={isNumericAccount ? "tel" : "text"}
            name="username"
            autoComplete="username"
            inputMode={isNumericAccount ? "numeric" : "text"}
            maxLength={isNumericAccount ? 11 : 32}
            placeholder={isDemoAccount ? "请输入演示账号" : "11 位数字账号或演示账号"}
            value={account}
            onChange={(e) => setAccount(e.target.value)}
          />
          <AuthInput
            label="登录密码"
            accent={theme.accent}
            type="password"
            name="password"
            autoComplete="current-password"
            placeholder="请输入登录密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            trailing={
              <Link
                href={registerHref}
                className="shrink-0 pl-3 text-[13px] font-medium"
                style={{ color: theme.accent }}
              >
                去注册
              </Link>
            }
          />
        </div>

        <div className="mt-4 rounded-xl border border-[#E3E8E4] bg-white/70 p-3.5">
          <div className="mb-2.5 flex flex-wrap items-center justify-between gap-1.5">
            <span className="text-[13px] font-medium text-[#3F4A46]">演示账号</span>
            <span className="text-[11px] text-[#7A8480]">
              统一密码：{DEMO_LOGIN_PASSWORD}
            </span>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {DEMO_LOGIN_ACCOUNTS.map(({ username, label, role }) => (
              <button
                key={username}
                type="button"
                onClick={() => {
                  setAccount(username);
                  setPassword(DEMO_LOGIN_PASSWORD);
                  setRemember(true);
                  setError("");
                }}
                className={`min-w-0 rounded-lg border px-2 py-2 text-left transition-colors ${
                  account === username
                    ? "border-[#1F7A4D]/40 bg-[#1F7A4D]/[0.07]"
                    : "border-[#E3E8E4] bg-white hover:border-[#1F7A4D]/30"
                }`}
                aria-pressed={account === username}
              >
                <span className="block truncate text-[12px] font-medium text-[#26312B]">
                  {label}
                </span>
                <span className="block truncate text-[10px] text-[#7A8480]">
                  {username} · {role}
                </span>
              </button>
            ))}
          </div>
        </div>

        <label className="mt-4 flex cursor-pointer items-center gap-2 text-[13px] text-[#5C6662]">
          <input
            type="checkbox"
            checked={remember}
          onChange={(e) => {
            const checked = e.target.checked;
            setRemember(checked);
            if (!checked) clearSavedUsername();
          }}
            style={{ accentColor: theme.accent }}
          />
          记住账号和密码，下次自动填充
        </label>

        <button
          type="submit"
          disabled={loading}
          className="mt-6 w-full rounded-xl py-3.5 text-[16px] font-medium text-white transition-colors disabled:cursor-not-allowed disabled:opacity-60"
          style={{ background: theme.accent }}
          onMouseEnter={(e) => {
            if (!loading) e.currentTarget.style.background = theme.accentHover;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = theme.accent;
          }}
        >
          {loading ? "处理中…" : `进入${selectedAssistant}`}
        </button>

        {/* 第三方登录占位：桌面保留（占位说明产品规划），移动端收掉——
            三枚 disabled 按钮在窄屏既占掉首屏高度又点不动，是纯噪音 */}
        <div className="hidden lg:block">
          <div className="my-5 flex items-center gap-3 text-[12px] text-[#98A29D]">
            <span className="h-px flex-1 bg-[#E4EAE6]" />
            或使用以下方式登录
            <span className="h-px flex-1 bg-[#E4EAE6]" />
          </div>
          <div className="grid grid-cols-3 gap-2.5">
            {["微信", "企业微信", "SSO"].map((name) => (
              <button
                key={name}
                type="button"
                disabled
                title="敬请期待"
                className="h-11 rounded-xl border border-[#E3E8E4] bg-white/85 text-[13px] text-[#3F4A46] opacity-60"
              >
                {name}
              </button>
            ))}
          </div>
        </div>
        <p className="mt-5 text-center text-[12px] text-[#98A29D] lg:mt-5">
          登录即代表你同意《服务条款》与《隐私政策》
        </p>

        {/* 注册入口（2026-10-07 手机端补）：原入口在左侧品牌区，≤lg 整块隐藏
            导致手机端没有可点的注册路径——移一条进表单卡，两端可见 */}
        <Link
          href={registerHref}
          className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-xl border border-[#1F7A4D]/15 bg-[#1F7A4D]/[0.06] py-3 text-[14px] font-medium text-[#1F7A4D] transition-colors hover:bg-[#1F7A4D]/[0.12]"
        >
          还没有账号？免费注册
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
            <path d="M2.6 8H13.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            <path d="M8.8 3.6L13.2 8L8.8 12.4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </Link>
      </form>
    </AuthShell>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
