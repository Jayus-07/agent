"use client";

import { useContext, useEffect, useRef, useState } from "react";
import { ChevronUp, LogOut, ShieldCheck, UserRound } from "lucide-react";
import { QueryClientContext } from "@tanstack/react-query";
import {
  atLeast,
  getCachedUser,
  logout,
  registerQueryClient,
  type QueryClientLike,
  type UserInfo,
} from "@/lib/auth";

const ROLE_LABEL: Record<string, string> = {
  viewer: "查看者",
  editor: "编辑者",
  admin: "管理员",
};

interface Props {
  collapsed?: boolean;
  /** 测试或显式集成时可传入当前 QueryClient；Provider 内则自动读取。 */
  queryClient?: QueryClientLike;
  /** 测试注入导航，生产默认只导航到一个固定登录地址。 */
  onNavigate?: (path: string) => void;
}

function roleOf(user: UserInfo | null): string {
  if (user?.platformRole && typeof user.platformRole === "string") {
    return user.platformRole;
  }
  return user?.roles?.find((role) => role in ROLE_LABEL) ?? "viewer";
}

export default function SidebarUserMenu({
  collapsed = false,
  queryClient,
  onNavigate,
}: Props) {
  const providerQueryClient = useContext(QueryClientContext);
  const activeQueryClient = queryClient ?? providerQueryClient;
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState<UserInfo | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  const logoutStarted = useRef(false);

  useEffect(() => {
    setUser(getCachedUser());
  }, []);

  useEffect(() => {
    if (!providerQueryClient) return undefined;
    return registerQueryClient(providerQueryClient);
  }, [providerQueryClient]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const role = roleOf(user);
  const displayName = user?.realName || user?.username || "当前用户";
  const admin = atLeast("admin") || role === "admin";

  const navigateToLogin = () => {
    if (onNavigate) {
      onNavigate("/login");
      return;
    }
    window.location.assign("/login");
  };

  const handleLogout = async () => {
    if (logoutStarted.current) return;
    logoutStarted.current = true;
    setLoggingOut(true);
    try {
      await logout(activeQueryClient ?? undefined);
    } finally {
      // logout 内部已统一清态；这里仅负责一次、固定目的地的页面跳转。
      navigateToLogin();
    }
  };

  return (
    <div
      className={`relative border-t border-black/5 py-2 ${collapsed ? "px-1" : "px-3"}`}
      data-testid="sidebar-user-menu"
    >
      <button
        type="button"
        aria-label="打开用户菜单"
        aria-expanded={open}
        disabled={loggingOut}
        onClick={() => setOpen((value) => !value)}
        className={`flex w-full items-center gap-2 rounded-lg text-left text-text-secondary transition-colors hover:bg-black/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none ${
          collapsed ? "justify-center p-2" : "px-2 py-2"
        }`}
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/10 text-accent">
          <UserRound size={15} aria-hidden="true" />
        </span>
        {!collapsed && (
          <span className="min-w-0 flex-1">
            <span className="block truncate text-xs font-medium text-text-primary">
              {displayName}
            </span>
            <span className="block truncate text-[10px] text-text-muted">
              {ROLE_LABEL[role] ?? "平台用户"}
            </span>
          </span>
        )}
        {!collapsed && <ChevronUp size={14} aria-hidden="true" />}
      </button>

      {open && (
        <div
          role="menu"
          aria-label="用户操作"
          className={`absolute z-30 rounded-xl border border-gray-200 bg-white p-1.5 shadow-card ${
            collapsed
              ? "bottom-1 left-full ml-2 w-52"
              : "bottom-14 left-3 right-3"
          }`}
        >
          <div className="border-b border-gray-100 px-3 py-2">
            <p className="truncate text-xs font-medium text-text-primary">{displayName}</p>
            <p className="mt-0.5 truncate text-[10px] text-text-muted">
              {ROLE_LABEL[role] ?? "平台用户"}
            </p>
          </div>
          {admin && (
            <a
              href="/settings/access"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="mt-1 flex items-center gap-2 rounded-lg px-3 py-2 text-xs text-text-secondary hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none"
            >
              <ShieldCheck size={14} aria-hidden="true" />
              访问控制
            </a>
          )}
          <button
            type="button"
            role="menuitem"
            aria-label="退出登录"
            data-testid="logout-button"
            disabled={loggingOut}
            onClick={() => void handleLogout()}
            className="mt-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs text-red-600 hover:bg-red-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-60 motion-reduce:transition-none"
          >
            <LogOut size={14} aria-hidden="true" />
            {loggingOut ? "正在退出…" : "退出登录"}
          </button>
        </div>
      )}
    </div>
  );
}
