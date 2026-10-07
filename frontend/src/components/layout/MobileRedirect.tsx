"use client";

/**
 * MobileRedirect — 移动端（≤md）把门户主页让位给登录页。
 *
 * 口径（2026-10-07 用户拍板）：三端里只有用户端适合手机展示，门户页在窄屏
 * 是「三张卡 → 再点用户端 → 才到登录」的空绕。故手机端直接落到 /login。
 *
 * 为什么用 matchMedia 而不是 CSS 隐藏：
 * - 纯 CSS 只能藏 UI，地址栏仍停在 /，用户刷新/分享链接时又回到门户；
 * - matchMedia 走 router.replace（不往 history 里塞一条），浏览器后退不会
 *   「后退一次又弹回门户」造成二次跳转循环；
 * - 仅在挂载后判定，首帧先渲染 null，避免桌面端闪一下空屏。
 *
 * 断点 768px 与 Tailwind `md` 对齐（globals.css 同样以 768 为界）。
 */
import { useEffect } from "react";
import { useRouter } from "next/navigation";

const MOBILE_QUERY = "(max-width: 767px)";

export default function MobileRedirect({ to = "/login" }: { to?: string }) {
  const router = useRouter();

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!window.matchMedia?.(MOBILE_QUERY).matches) return;
    // replace 而非 push：不污染历史栈，后退键行为符合直觉
    router.replace(to);
  }, [router, to]);

  return null;
}
