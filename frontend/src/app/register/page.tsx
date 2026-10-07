"use client";

/**
 * /register — 注册页（统一门户视觉体系 · 绿色主题）
 *
 * 字段：手机号（必选，作为注册账号）/ 邮箱（选填）/ 设置密码 / 确认密码 /
 *       企业名称（选填）/ 协议勾选。
 * 复用 lib/auth.register + login：注册成功后自动登录并跳回 redirect（默认 /agent）。
 *
 * 后端契约说明：当前 /api/sys/users/register 仅持久化 username/password/realName，
 * 邮箱、企业名称作为附加字段发送（后端暂忽略），后续扩展后端模型后即可落库。
 */
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { consumeExpiredFlag, login, register, saveUsername } from "@/lib/auth";
import AuthShell, { AuthNav } from "@/components/auth/AuthShell";
import AuthInput from "@/components/auth/AuthInput";

const ACCENT = "#1F7A4D";
const ACCENT_HOVER = "#17633d";
const FOG: [string, string, string, string] = [
  "radial-gradient(circle at 30% 30%, rgba(80,160,110,0.55), transparent 70%)",
  "radial-gradient(circle at 72% 18%, rgba(120,195,150,0.45), transparent 70%)",
  "radial-gradient(circle at 82% 82%, rgba(50,110,75,0.5), transparent 70%)",
  "radial-gradient(circle at 8% 92%, rgba(200,225,205,0.6), transparent 70%)",
];

function RegisterForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirect = searchParams.get("redirect") || "/agent";

  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [company, setCompany] = useState("");
  const [agree, setAgree] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (consumeExpiredFlag()) setNotice("登录已过期，请重新登录");
  }, []);

  const goNext = () => {
    router.replace(redirect.startsWith("/") ? redirect : "/agent");
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (loading) return;
    setError("");
    setNotice("");

    if (!/^1[3-9]\d{9}$/.test(phone.trim())) {
      setError("请输入有效的 11 位手机号");
      return;
    }
    if (email.trim() && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) {
      setError("邮箱格式不正确");
      return;
    }
    if (password.length < 8 || password.length > 20) {
      setError("密码长度需为 8-20 个字符");
      return;
    }
    if (!/[A-Za-z]/.test(password) || !/\d/.test(password)) {
      setError("密码需同时包含字母与数字");
      return;
    }
    if (password !== confirm) {
      setError("两次输入的密码不一致");
      return;
    }
    if (!agree) {
      setError("请先阅读并同意《服务条款》与《隐私政策》");
      return;
    }

    setLoading(true);
    try {
      // 手机号作为注册账号；邮箱 / 企业名称为 best-effort 附加字段
      await register(phone.trim(), password, confirm, company.trim() || undefined);
      await login(phone.trim(), password);
      saveUsername(phone.trim());
      goNext();
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败，请稍后重试");
      setLoading(false);
    }
  };

  const left = (
    <div className="max-w-[520px]">
      <span className="inline-flex items-center rounded-full border border-black/10 bg-white/70 px-3.5 py-1.5 text-[13px] text-[#4A544F]">
        AI 问答 · 多模态客服
      </span>
      <h1 className="mt-7 whitespace-pre-line text-[56px] font-bold leading-[68px] tracking-[-1.5px] text-[#16191A]">
        {"注册一个账号，\n问题就能问到底"}
      </h1>
      <p className="mt-6 max-w-[470px] text-[16px] leading-7 text-[#6E7873]">
        知识库问答、图片与文档理解、订单与售后查询全部开放；答不上来的问题直接转人工客服继续跟。
      </p>
      <Link
        href="/login"
        className="mt-8 inline-flex items-center gap-2 rounded-full border border-black/10 bg-white/70 px-6 py-3.5 text-[15px] font-medium text-[#16191A] transition-colors hover:bg-white"
      >
        已有账号，立即登录
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M2.6 8H13.2" stroke="#16191A" strokeWidth="1.6" strokeLinecap="round" />
          <path
            d="M8.8 3.6L13.2 8L8.8 12.4"
            stroke="#16191A"
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
      accent={ACCENT}
      accentHover={ACCENT_HOVER}
      fog={FOG}
      nav={<AuthNav brand="智能协作平台" links={[{ label: "返回官网" }, { label: "帮助中心" }]} />}
      left={left}
    >
      <div className="mb-5 lg:mb-6">
        <span className="mb-3 inline-flex items-center rounded-full border border-black/10 bg-white/70 px-3 py-1 text-[12px] text-[#4A544F] lg:hidden">
          AI 问答 · 多模态客服
        </span>
        <h2 className="text-[22px] font-semibold text-[#16191A] sm:text-[24px] lg:text-[26px]">创建你的账号</h2>
        <p className="mt-1.5 text-[13px] text-[#7A8480]">30 秒完成注册，马上开始提问</p>
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
            label="手机号"
            accent={ACCENT}
            type="tel"
            autoComplete="tel"
            placeholder="请输入 11 位手机号"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
          />
          <AuthInput
            label="邮箱（选填）"
            accent={ACCENT}
            type="email"
            autoComplete="email"
            placeholder="用于找回密码与接收通知"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <AuthInput
            label="设置密码"
            accent={ACCENT}
            type="password"
            autoComplete="new-password"
            placeholder="8-20 位，需包含字母与数字"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <AuthInput
            label="确认密码"
            accent={ACCENT}
            type="password"
            autoComplete="new-password"
            placeholder="请再次输入密码"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
          <AuthInput
            label="企业 / 团队名称（选填）"
            accent={ACCENT}
            type="text"
            placeholder="用于创建你的工作空间"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
          />
        </div>

        <label className="mt-4 flex cursor-pointer items-start gap-2 text-[13px] text-[#5C6662]">
          <input
            type="checkbox"
            checked={agree}
            onChange={(e) => setAgree(e.target.checked)}
            style={{ accentColor: ACCENT }}
            className="mt-0.5"
          />
          <span>
            我已阅读并同意《服务条款》与《隐私政策》
          </span>
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
          {loading ? "处理中…" : "创建账号"}
        </button>

        <p className="mt-5 text-center text-[12px] text-[#98A29D]">
          注册即代表你同意《服务条款》与《隐私政策》
        </p>
      </form>
    </AuthShell>
  );
}

export default function RegisterPage() {
  return (
    <Suspense fallback={null}>
      <RegisterForm />
    </Suspense>
  );
}
