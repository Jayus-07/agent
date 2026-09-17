"use client";

/**
 * /login — 登录页（方案 A · 居中单卡片）
 *
 * 视觉遵循全站「AI Native 极简商务风」：#4D6BFE 主色、浅灰底、
 * 输入框 focus 光圈、细腻卡片投影。不引入新依赖。
 *
 * 行为：
 * - 登录成功 → 跳 redirect 参数指定的原页面（默认 /）
 * - 会话被 401 拦截器踢回时显示"登录已过期"提示（sessionStorage 标记）
 * - 错误内联展示（密码错误 / 网络异常），不用 alert
 * - 记住账号和密码：勾选后 localStorage 保存账号与密码（Base64 防肉眼直读，
 *   非加密——2026-09-17 应用户明确要求加入，仅限本机环境），下次自动预填
 * - 2026-09-17 应用户要求移除注册入口：管理端账号由管理员统一开通
 *   （种子账号 admin/admin 由 DBA/脚本写入 auth.users）
 */
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  clearSavedPassword,
  clearSavedUsername,
  consumeExpiredFlag,
  getSavedPassword,
  getSavedUsername,
  login,
  saveCredentials,
} from "@/lib/auth";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirect = searchParams.get("redirect") || "/";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);

  // 挂载：预填"记住的账号和密码"（勾选过记住密码则账号密码一并回填）+ 会话过期标记
  useEffect(() => {
    const saved = getSavedUsername();
    if (saved) {
      setUsername(saved);
      setRemember(true);
      const savedPwd = getSavedPassword();
      if (savedPwd) setPassword(savedPwd);
    }
    if (consumeExpiredFlag()) {
      setNotice("登录已过期，请重新登录");
    }
  }, []);

  const goNext = () => {
    router.replace(redirect.startsWith("/") ? redirect : "/");
  };

  const doLogin = async (name: string, pass: string, persist: boolean) => {
    setLoading(true);
    setError("");
    try {
      await login(name, pass);
      if (persist) saveCredentials(name, pass);
      else {
        clearSavedUsername();
        clearSavedPassword();
      }
      goNext();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请稍后重试");
      setLoading(false);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (loading) return;
    setError("");
    setNotice("");

    if (!username.trim() || !password) {
      setError("请输入账号和密码");
      return;
    }
    await doLogin(username.trim(), password, remember);
  };

  const inputStyle = {
    borderColor: "var(--border-subtle)",
    color: "var(--text-primary)",
    boxShadow: "var(--shadow-input)",
  };
  const onFocus = (e: React.FocusEvent<HTMLInputElement>) => {
    e.target.style.borderColor = "var(--accent)";
    e.target.style.boxShadow = "0 0 0 3px rgba(77, 107, 254, 0.12)";
  };
  const onBlur = (e: React.FocusEvent<HTMLInputElement>) => {
    e.target.style.borderColor = "var(--border-subtle)";
    e.target.style.boxShadow = "var(--shadow-input)";
  };

  return (
    <main
      className="flex min-h-screen items-center justify-center px-4"
      style={{ background: "var(--bg-root)" }}
    >
      <div
        className="w-full max-w-[340px] rounded-xl border bg-white px-7 pb-6 pt-7"
        style={{
          borderColor: "var(--border-subtle)",
          boxShadow: "0 4px 16px rgba(26, 26, 46, 0.05)",
        }}
      >
        {/* 品牌区 */}
        <div className="mb-1 flex items-center gap-2.5">
          <div
            className="flex h-[34px] w-[34px] items-center justify-center rounded-[9px]"
            style={{ background: "var(--accent)" }}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
              <path
                d="M3 12L8 3l5 9"
                stroke="#fff"
                strokeWidth="1.8"
                fill="none"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <circle cx="8" cy="10" r="1.6" fill="#fff" />
            </svg>
          </div>
          <h1 className="text-[15px] font-medium" style={{ color: "var(--text-primary)" }}>
            欢迎回来
          </h1>
        </div>
        <p className="mb-4 text-[13px]" style={{ color: "var(--text-muted)" }}>
          登录 AI Agent 管理工作台
        </p>

        {/* 提示条（内联） */}
        {notice && (
          <div
            className="mb-4 rounded-lg px-3 py-2.5 text-[12px]"
            style={{ background: "#FAEEDA", color: "#633806" }}
          >
            {notice}
          </div>
        )}
        {error && (
          <div
            className="mb-4 rounded-lg px-3 py-2.5 text-[12px]"
            style={{ background: "#FCEBEB", color: "#791F1F" }}
          >
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} noValidate>
          <label className="mb-1.5 block text-[12px]" style={{ color: "var(--text-secondary)" }}>
            账号
          </label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            className="mb-3.5 w-full rounded-lg border bg-white px-3 py-2 text-[13px] outline-none transition-shadow"
            style={inputStyle}
            onFocus={onFocus}
            onBlur={onBlur}
          />

          <label className="mb-1.5 block text-[12px]" style={{ color: "var(--text-secondary)" }}>
            密码
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            className="w-full rounded-lg border bg-white px-3 py-2 text-[13px] outline-none transition-shadow"
            style={inputStyle}
            onFocus={onFocus}
            onBlur={onBlur}
          />

          <label
            className="mt-3 flex cursor-pointer items-center gap-1.5 text-[12px]"
            style={{ color: "var(--text-secondary)" }}
          >
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              style={{ accentColor: "var(--accent)" }}
            />
            记住账号和密码（本机保存，下次自动填充）
          </label>

          <button
            type="submit"
            disabled={loading}
            className="mt-5 w-full rounded-lg py-2.5 text-[13px] font-medium text-white transition-colors disabled:cursor-not-allowed disabled:opacity-60"
            style={{ background: "var(--accent)" }}
            onMouseEnter={(e) => {
              if (!loading) e.currentTarget.style.background = "var(--accent-hover)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "var(--accent)";
            }}
          >
            {loading ? "处理中…" : "登 录"}
          </button>
        </form>

        <p className="mt-4 text-center text-[11px]" style={{ color: "var(--text-muted)" }}>
          账号由管理员统一开通
        </p>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
