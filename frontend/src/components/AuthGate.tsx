"use client";

/**
 * AuthGate — 全局路由守卫（客户端方案，未引入 middleware）
 *
 * 规则：
 * - /login 为公开页，直接放行；
 * - 其余路径：有 access token → 放行；无 token 时先试 HttpOnly Cookie
 *   静默续期（tryRefreshOnce，single-flight），仍失败 → 跳 /login 并带
 *   redirect 参数（登录成功后由登录页跳回原路径）。
 * - 检查期间渲染 null，避免受保护内容闪烁。
 *
 * 复用 lib/auth 的既有约定：token 存 sessionStorage（agent.access_token），
 * 401 时的兜底拦截仍在 `@/api/client` 的 handleAuthFailure，两者互补：
 * 这里拦"进入页面时就没有 token"，那里拦"请求中途 token 失效"。
 */
import { useEffect, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { getAccessToken, tryRefreshOnce } from "@/lib/auth";

export default function AuthGate({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const isPublic = pathname.startsWith("/login");
  const [state, setState] = useState<"checking" | "ok">("checking");

  useEffect(() => {
    if (isPublic) {
      setState("ok");
      return;
    }
    if (getAccessToken()) {
      setState("ok");
      return;
    }
    let cancelled = false;
    setState("checking");
    tryRefreshOnce().then((ok) => {
      if (cancelled) return;
      if (ok) {
        setState("ok");
      } else {
        const redirect = encodeURIComponent(pathname);
        window.location.assign(`/login?redirect=${redirect}`);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [pathname, isPublic]);

  if (isPublic) return <>{children}</>;
  if (state !== "ok") return null;
  return <>{children}</>;
}
