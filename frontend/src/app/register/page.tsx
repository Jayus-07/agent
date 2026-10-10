"use client";

/**
 * /register — 注册页（统一门户视觉体系 · 绿色主题）
 *
 * 字段：11 位数字账号 / 设置密码 / 确认密码 / 图形验证码 / 协议勾选。
 * 复用 lib/auth.register：注册成功后返回登录页，带入刚注册的账号密码。
 *
 * 数字账号作为 username 保存；只有符合标准手机号格式时才额外保存回访手机号。
 */
import { Suspense, useCallback, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { RefreshCw } from "lucide-react";
import { consumeExpiredFlag, fetchRegisterCaptcha, register, setPendingLoginCredentials } from "@/lib/auth";
import type { RegisterCaptcha } from "@/lib/auth";
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
  const selectedAssistant = redirect.startsWith("/customer-service")
    ? "智能客服"
    : redirect.startsWith("/travel")
      ? "旅游助手"
      : "企业助手";
  const loginHref = `/login?redirect=${encodeURIComponent(redirect)}`;

  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [agree, setAgree] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);

  // 图形验证码（2026-10-07）：4 位纯数字，一码一用——提交后无论对错都换图
  const [captcha, setCaptcha] = useState<RegisterCaptcha | null>(null);
  const [captchaCode, setCaptchaCode] = useState("");
  const [captchaLoading, setCaptchaLoading] = useState(true);

  const refreshCaptcha = useCallback(async () => {
    setCaptchaLoading(true);
    setCaptchaCode("");
    try {
      setCaptcha(await fetchRegisterCaptcha());
    } catch {
      setCaptcha(null);
    } finally {
      setCaptchaLoading(false);
    }
  }, []);

  useEffect(() => { void refreshCaptcha(); }, [refreshCaptcha]);

  const svgDataUri = captcha
    ? `data:image/svg+xml;utf8,${encodeURIComponent(captcha.svg)}`
    : null;

  useEffect(() => {
    if (consumeExpiredFlag()) setNotice("登录已过期，请重新登录");
  }, []);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (loading) return;
    setError("");
    setNotice("");

    if (!/^\d{11}$/.test(phone.trim())) {
      setError("请输入 11 位数字");
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
    // 验证码空着就别发请求了：后端一码一用，空提交也会作废刚领的码
    if (!captchaCode.trim()) {
      setError("请输入图形验证码");
      return;
    }

    setLoading(true);
    try {
      // 2026-10-07 用户拍板：注册只收 手机号+密码+验证码（邮箱/企业名字段已移除）
      try {
        // 登录账号只使用 11 位数字；不把它作为回访手机号写入客户资料。
        await register(
          phone.trim(),
          password,
          confirm,
          undefined,
          captcha ? { ticket: captcha.ticket, code: captchaCode.trim() } : undefined,
          /^1\d{10}$/.test(phone.trim()) ? phone.trim() : undefined,
        );
      } catch (regErr) {
        // 验证码一码一用：无论对错都已销毁，换图让用户重填
        void refreshCaptcha();
        throw regErr;
      }
      setPendingLoginCredentials(phone.trim(), password);
      router.replace(`${loginHref}&registered=1`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败，请稍后重试");
      setLoading(false);
    }
  };

  const left = (
    <div className="max-w-[520px]">
      <span className="inline-flex items-center rounded-full border border-black/10 bg-white/70 px-3.5 py-1.5 text-[13px] text-[#4A544F]">
        即将进入：{selectedAssistant}
      </span>
      <h1 className="mt-7 whitespace-pre-line text-[56px] font-bold leading-[68px] tracking-[-1.5px] text-[#16191A]">
        {"创建账号，\n和助手开始聊"}
      </h1>
      <p className="mt-6 max-w-[470px] text-[16px] leading-7 text-[#6E7873]">
        注册完成后会直接进入{selectedAssistant}。同一账号也能从门户切换其他助手。
      </p>
      <Link
        href={loginHref}
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
      nav={<AuthNav brand="智能助手平台" links={[{ label: "返回门户", href: "/" }]} badge="统一入口" />}
      left={left}
    >
      <div className="mb-5 lg:mb-6">
        <span className="mb-3 inline-flex items-center rounded-full border border-black/10 bg-white/70 px-3 py-1 text-[12px] text-[#4A544F] lg:hidden">
          即将进入：{selectedAssistant}
        </span>
        <h2 className="text-[22px] font-semibold text-[#16191A] sm:text-[24px] lg:text-[26px]">创建你的账号</h2>
        <p className="mt-1.5 text-[13px] leading-5 text-[#7A8480]">用 11 位数字账号和密码注册；密码需为 8–20 位且包含字母和数字，完成图形验证码即可提交，无需短信验证码。注册后将进入{selectedAssistant}。</p>
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
            label="11 位数字账号"
            accent={ACCENT}
            type="tel"
            name="username"
            autoComplete="username"
            placeholder="请输入 11 位数字账号"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
          />
          <AuthInput
            label="设置密码"
            accent={ACCENT}
            type="password"
            name="new-password"
            autoComplete="new-password"
            placeholder="8-20 位，需包含字母与数字"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <AuthInput
            label="确认密码"
            accent={ACCENT}
            type="password"
            name="confirm-password"
            autoComplete="new-password"
            placeholder="请再次输入密码"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </div>

        {/* 图形验证码（2026-10-07）：4 位纯数字，点图/刷新按钮换一张，
            inputmode=numeric 让手机直弹数字键盘 */}
        <div className="mt-4">
          <label htmlFor="register-captcha" className="mb-1.5 block text-[13px] text-[#3F4A46]">
            图形验证码
          </label>
          <div className="flex items-center gap-2.5">
            <input
              id="register-captcha"
              type="text"
              inputMode="numeric"
              autoComplete="off"
              maxLength={4}
              placeholder="输入图中 4 位数字"
              value={captchaCode}
              onChange={(e) => setCaptchaCode(e.target.value.replace(/\D/g, ""))}
              className="h-12 min-w-0 flex-1 rounded-xl border border-[#E3E8E4] bg-white px-3.5
                text-[16px] text-[#16191A] placeholder:text-[#98A29D] outline-none
                focus:border-[#1F7A4D] transition-colors"
            />
            {/* 验证码图（后端手绘 SVG 转 data-URI 内联，无外部请求）；点击图也可刷新 */}
            <button
              type="button"
              onClick={() => void refreshCaptcha()}
              disabled={captchaLoading}
              aria-label="换一张验证码"
              title="看不清？换一张"
              className="shrink-0 overflow-hidden rounded-xl border border-[#E3E8E4] transition-opacity hover:opacity-80 disabled:opacity-60"
            >
              {svgDataUri ? (
                <img src={svgDataUri} alt="图形验证码" width={86} height={42} className="block" />
              ) : (
                <span className="flex h-[42px] w-[86px] items-center justify-center bg-[#F4F6F3] text-[11px] text-[#98A29D]">
                  {captchaLoading ? "加载中" : "点击获取"}
                </span>
              )}
            </button>
            <button
              type="button"
              onClick={() => void refreshCaptcha()}
              disabled={captchaLoading}
              aria-label="刷新验证码"
              title="换一张"
              className="shrink-0 rounded-lg p-2 text-[#1F7A4D] transition-colors hover:bg-[#1F7A4D]/[0.08] disabled:opacity-50"
            >
              <RefreshCw size={16} className={captchaLoading ? "animate-spin" : ""} />
            </button>
          </div>
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
          {loading ? "处理中…" : "注册并开始使用"}
        </button>

        {/* 登录入口（2026-10-07 手机端补）：与登录页注册入口对称，两端可见 */}
        <Link
          href={loginHref}
          className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-xl border border-[#1F7A4D]/15 bg-[#1F7A4D]/[0.06] py-3 text-[14px] font-medium text-[#1F7A4D] transition-colors hover:bg-[#1F7A4D]/[0.12]"
        >
          已有账号？直接登录
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
            <path d="M2.6 8H13.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            <path d="M8.8 3.6L13.2 8L8.8 12.4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </Link>

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
