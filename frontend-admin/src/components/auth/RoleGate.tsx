"use client";

/**
 * RoleGate — 管理端敏感页角色守卫（UX 设计 P0-①，对应 v3 计划 P2-1 的 UI 层）
 *
 * 堵的洞：nav 已按角色过滤（navConfig.tsx 过滤 NAV），但 viewer 直输 URL
 * 仍可到达页面（见 admin navConfig 注释自证）。本组件在 route segment
 * layout 层同步判定：角色不足 → 页内 ForbiddenCard（保留导航上下文，
 * 不整页跳转、不闪屏）。
 *
 * 角色来源：lib/auth.getRoles()/atLeast()（login 时写入的 userInfo.roles，
 * 与后端 auth.users.role 枚举 viewer/editor/admin 同构）。
 * 权限真闸在后端（403 兜底），本组件只是体验层——后端拒了的请求这里
 * 不会提前放行任何数据。
 *
 * 判定语义：fail-safe。roles 未知（sessionStorage 无 user_info，如重启
 * 浏览器后仅凭 Cookie 静默续期成功）一律按无权限处理，卡片引导重新登录；
 * 运维逃生开关：NEXT_PUBLIC_ROLE_GATE_DISABLED=1 时整体放行（可关，v3 P2-1 验收项）。
 *
 * 无水合风险：本组件只出现在 AuthGate 子树内，AuthGate 检查期渲染 null
 * （见 AuthGate L52），故本组件不会在 SSR 产物中出现。
 */
import type { ReactNode } from "react";
import { atLeast, type RoleName } from "@/lib/auth";
import ForbiddenCard from "@/components/shared/ForbiddenCard";

const GUARD_DISABLED = process.env.NEXT_PUBLIC_ROLE_GATE_DISABLED === "1";

export default function RoleGate({
  minRole = "admin",
  pageName,
  detail,
  children,
}: {
  /** 允许访问的最低角色，默认 admin（多角色取最高，与后端 _ROLE_RANK 同构） */
  minRole?: RoleName;
  /** 页面业务名，展示在 403 卡片标题 */
  pageName?: string;
  /** 403 卡片详情折叠区自定义内容 */
  detail?: ReactNode;
  children: ReactNode;
}) {
  if (GUARD_DISABLED) return <>{children}</>;
  if (!atLeast(minRole)) {
    return <ForbiddenCard pageName={pageName} detail={detail} />;
  }
  return <>{children}</>;
}
