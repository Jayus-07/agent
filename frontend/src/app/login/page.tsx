"use client";

/**
 * /login — 用户端登录页（统一门户视觉体系 · 分屏 + 毛玻璃卡）
 *
 * - 绿色主题，复用既有登录态逻辑：redirect 回跳、记住用户名（仅用户名，密码不落盘）、
 *   会话过期标记（consumeExpiredFlag）。第三方登录为占位（禁用·敬请期待）。
 * - 客服端登录已分离：客服端现为独立应用 frontend-cs（:3300），由门户主页跨应用跳转，
 *   不再经此页（原 ?end=cs 主题变体已移除）。
 * - 注册入口移到独立 /register 页（见设计稿 02）。
 */
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  clearSavedUsername,
  consumeExpiredFlag,
  getSavedUsername,
  login,
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

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirect = searchParams.get("redirect") || "/agent";
  const theme = THEME;

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
    if (consumeExpiredFlag()) {
      setNotice("登录已过期，请重新登录");
    }
  }, []);

  const goNext = () => {
    router.replace(redirect.startsWith("/") ? redirect : "/agent");
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (loading) return;
    setError("");
    setNotice("");
    if (!account.trim() || !password) {
      setError("请输入手机号 / 邮箱和密码");
      return;
    }
    setLoading(true);
    try {
      const result = await login(account.trim(), password);
      if (remember) saveUsername(account.trim());
      else clearSavedUsername();
      // 临时密码首次登录（P6.3/TD-04）：先改密，改完由改密页进 /agent
      if (result.mustChangePassword) {
        router.replace("/change-password");
        return;
      }
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
        AI 问答 · 多模态客服
      </span>
      <h1
        className="mt-7 whitespace-pre-line text-[56px] font-bold leading-[68px] tracking-[-1.5px] text-[#16191A]"
      >
        {"问一句，\n拿到带证据的答案"}
      </h1>
      <p className="mt-6 max-w-[470px] text-[16px] leading-7 text-[#6E7873]">
        知识库检索、图片与文档理解、订单与售后查询，一句话全部接住；AI 答不了的问题，自动转人工客服接着办。
      </p>
      <Link
        href="/register"
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
          brand={"智能协作平台"}
          links={[{ label: "返回官网" }, { label: "帮助中心" }]}
        />
      }
      left={left}
    >
      <div className="mb-5 lg:mb-6">
        <span className="mb-3 inline-flex items-center rounded-full border border-black/10 bg-white/70 px-3 py-1 text-[12px] text-[#4A544F] lg:hidden">
          AI 问答 · 多模态客服
        </span>
        <h2 className="text-[22px] font-semibold text-[#16191A] sm:text-[24px] lg:text-[26px]">
          登录你的 AI 助手
        </h2>
        <p className="mt-1.5 text-[13px] text-[#7A8480]">
          继续上次的对话、知识库与客服工单
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
            label={"手机号 / 邮箱"}
            accent={theme.accent}
            type="text"
            autoComplete="username"
            placeholder="请输入手机号或邮箱"
            value={account}
            onChange={(e) => setAccount(e.target.value)}
          />
          <AuthInput
            label="登录密码"
            accent={theme.accent}
            type="password"
            autoComplete="current-password"
            placeholder="请输入登录密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            trailing={
              <Link
                href="/register"
                className="shrink-0 pl-3 text-[13px] font-medium"
                style={{ color: theme.accent }}
              >
                忘记密码？
              </Link>
            }
          />
        </div>

        <label className="mt-4 flex cursor-pointer items-center gap-2 text-[13px] text-[#5C6662]">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            style={{ accentColor: theme.accent }}
          />
          记住登录状态
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
          {loading ? "处理中…" : "登录"}
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
          href="/register"
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
