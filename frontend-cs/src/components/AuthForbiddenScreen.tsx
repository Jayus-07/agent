"use client";

/**
 * AuthForbiddenScreen — 客服工作台整页 403（AuthGate forbidden 分支专用）
 *
 * 与管理端 AuthForbiddenScreen 同款交互（2026-09-29 对齐），与
 * shared/ForbiddenCard（已进工作台、单页 403 的页内卡片，保留侧栏上下文）
 * 的分工：这里是连工作台外壳都进不去时的整页替代——首页同样被门禁拦，
 * 「返回首页」只会死循环，所以只提供「切换账号」一条出路：先 logout 吊销
 * 会话并清本地态，再带 redirect 回登录页；切号成功后由登录页的 redirect
 * 逻辑回跳原目标页。
 */
import { useState } from "react";
import { LogOut, ShieldAlert } from "lucide-react";
import { getCachedUser, logout, type CsRoleName, type UserInfo } from "@/lib/auth";

const ROLE_LABEL: Record<string, string> = {
  viewer: "查看者",
  editor: "编辑者",
  admin: "管理员",
  super_admin: "超级管理员",
};

const CS_ROLE_LABEL: Record<CsRoleName, string> = {
  agent: "坐席",
  supervisor: "主管",
};

// 与 SidebarUserMenu.roleOf 同口径：platformRole 优先，roles 兜底
function roleOf(user: UserInfo | null): string {
  if (user?.platformRole && typeof user.platformRole === "string") {
    return user.platformRole;
  }
  return user?.roles?.find((role) => role in ROLE_LABEL) ?? "viewer";
}

function csRoleLabel(user: UserInfo | null): string {
  if (user?.csRole === "agent" || user?.csRole === "supervisor") {
    return CS_ROLE_LABEL[user.csRole];
  }
  return "未绑定坐席";
}

export default function AuthForbiddenScreen({
  pathname,
  onNavigate,
}: {
  pathname: string;
  /** 测试注入导航；生产默认 logout 后跳 /login 并带 redirect 回跳参数。 */
  onNavigate?: (path: string) => void;
}) {
  const [switching, setSwitching] = useState(false);
  const user = getCachedUser();
  const displayName = user?.realName || user?.username || "当前用户";

  const handleSwitch = async () => {
    if (switching) return;
    setSwitching(true);
    try {
      // logout 内部无论网络成败都会清本地登录态；这里只负责随后的一次跳转。
      await logout();
    } finally {
      const target = `/login?redirect=${encodeURIComponent(pathname)}`;
      if (onNavigate) onNavigate(target);
      else window.location.assign(target);
    }
  };

  return (
    <div
      role="alert"
      className="flex min-h-screen w-full items-center justify-center bg-[var(--bg-root)] p-6"
    >
      <div className="w-full max-w-md rounded-2xl border border-gray-200 bg-white p-8 shadow-card">
        <div className="flex items-center gap-3">
          <span
            aria-hidden="true"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-red-50 text-red-600"
          >
            <ShieldAlert size={20} />
          </span>
          <div>
            <h1 className="text-base font-semibold text-gray-900">
              无客服工作台访问权限
            </h1>
            <p className="mt-0.5 text-xs text-gray-500">
              HTTP 403 · 需要客服坐席（agent/supervisor）绑定
            </p>
          </div>
        </div>

        <div className="mt-4 rounded-lg bg-gray-50 p-4 text-sm leading-relaxed text-gray-700">
          <p>
            当前账号{" "}
            <span className="font-medium text-gray-900">{displayName}</span>
            （平台角色：{ROLE_LABEL[roleOf(user)] ?? "平台用户"}｜客服角色：
            {csRoleLabel(user)}）无权访问客服坐席工作台。请切换坐席账号登录，
            或联系管理员将你的账号加入客服坐席名单。
          </p>
        </div>

        <button
          type="button"
          onClick={handleSwitch}
          disabled={switching}
          className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-60 motion-reduce:transition-none"
        >
          <LogOut size={15} aria-hidden="true" />
          {switching ? "正在退出登录…" : "切换账号"}
        </button>
      </div>
    </div>
  );
}
