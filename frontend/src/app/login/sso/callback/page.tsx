"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  completeSsoLogin,
  safeRedirectPath,
  type SsoProvider,
} from "@/lib/auth";

const PROVIDERS = new Set<SsoProvider>(["feishu", "wecom", "dingtalk"]);

export default function SsoCallbackPage() {
  const router = useRouter();
  const [message, setMessage] = useState("正在核验企业身份…");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const params = new URLSearchParams(window.location.search);
    const provider = params.get("provider") as SsoProvider | null;
    const code = params.get("code") || params.get("authCode") || "";
    const state = params.get("state") || "";
    const providerError = params.get("error");
    // 授权码只短暂保留在页面内存中，尽快从地址栏和浏览器历史移除。
    window.history.replaceState({}, document.title, "/login/sso/callback");

    if (providerError) {
      setError("你取消了企业账号授权，或企业平台未完成登录。");
      return () => { active = false; };
    }
    if (!provider || !PROVIDERS.has(provider) || !code || !state) {
      setError("登录回调信息不完整，请返回登录页重新扫码。");
      return () => { active = false; };
    }

    completeSsoLogin(provider, code, state)
      .then(({ redirectTo }) => {
        if (!active) return;
        setMessage("身份已确认，正在进入系统…");
        router.replace(safeRedirectPath(redirectTo, "/agent"));
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "扫码登录失败，请重试。");
      });

    return () => { active = false; };
  }, [router]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#F4F8F5] px-5 py-12">
      <section
        aria-live="polite"
        className="w-full max-w-[420px] rounded-2xl border border-[#DDE8E0] bg-white px-7 py-9 text-center shadow-[0_18px_55px_rgba(31,75,50,0.08)] sm:px-10"
      >
        <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-full bg-[#E8F4EB] text-[#1F7A4D]">
          {error ? (
            <span className="text-xl font-semibold" aria-hidden="true">!</span>
          ) : (
            <span className="h-5 w-5 animate-spin rounded-full border-2 border-[#1F7A4D]/25 border-t-[#1F7A4D] motion-reduce:animate-none" />
          )}
        </div>
        <h1 className="text-xl font-semibold text-[#1B2921]">
          {error ? "暂时无法登录" : "正在登录企业助手"}
        </h1>
        <p className="mt-3 break-words text-sm leading-6 text-[#607067]">
          {error || message}
        </p>
        {error && (
          <Link
            href="/login"
            className="mt-6 inline-flex min-h-11 items-center justify-center rounded-xl bg-[#1F7A4D] px-5 text-sm font-medium text-white transition-colors hover:bg-[#17633D] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#1F7A4D]"
          >
            返回登录页
          </Link>
        )}
      </section>
    </main>
  );
}
