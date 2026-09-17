"use client";

/**
 * ForbiddenCard — 页内 403 卡片（UX 设计 P0-①，三段式错误反馈规范 §4.4）
 *
 * 三段式：发生了什么 / 我能做什么 / 详情（折叠）。
 * 与 RoleGate 配套：viewer 直输 URL 时不再看到后端原始 403 文案或一屏报错，
 * 而是保留侧栏导航上下文的页内卡片。权限真闸仍在后端（403 兜底），
 * 本卡片只是体验层呈现，不做任何权限判定。
 */
import { useState, type ReactNode } from "react";

export default function ForbiddenCard({
  /** 页面业务名，如「可观测性」「评估中心」；缺省用通用文案 */
  pageName,
  /** 详情折叠区自定义内容（如所需角色、后端错误码）；缺省给标准说明 */
  detail,
}: {
  pageName?: string;
  detail?: ReactNode;
}) {
  const [openDetail, setOpenDetail] = useState(false);

  return (
    <div className="flex flex-1 items-start justify-center p-6">
      <div className="mt-8 w-full max-w-md rounded-xl border border-gray-200 bg-white p-6 shadow-card">
        <div className="flex items-center gap-3">
          <span
            aria-hidden="true"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-red-50 text-red-600"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="4" y="10" width="16" height="10" rx="2" />
              <path d="M8 10V7a4 4 0 0 1 8 0v3" />
            </svg>
          </span>
          <div>
            <h2 className="text-base font-medium text-gray-900">
              没有访问{pageName ? `「${pageName}」` : "此页面"}的权限
            </h2>
            <p className="mt-0.5 text-xs text-gray-500">HTTP 403 · 角色不足</p>
          </div>
        </div>

        <div className="mt-4 rounded-lg bg-gray-50 p-4">
          <p className="text-sm text-gray-700">
            <span className="font-medium">我能做什么：</span>
            该区域仅对管理员开放。如需访问，请联系管理员为你的账号分配 admin
            角色，重新登录后生效。
          </p>
          <div className="mt-3 flex gap-2">
            <a
              href="/"
              className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover"
            >
              返回控制台首页
            </a>
            <a
              href="/login"
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50"
            >
              切换账号登录
            </a>
          </div>
        </div>

        <button
          type="button"
          onClick={() => setOpenDetail((v) => !v)}
          className="mt-3 text-xs text-gray-400 hover:text-gray-600"
          aria-expanded={openDetail}
        >
          {openDetail ? "收起详情 ▲" : "查看详情 ▼"}
        </button>
        {openDetail && (
          <div className="mt-2 rounded-lg border border-gray-100 bg-gray-50 p-3 text-xs leading-relaxed text-gray-500">
            {detail ?? (
              <>
                <p>· 页面已打开，但当前账号角色不含 admin（角色由登录态携带，重新登录后更新）。</p>
                <p>· 本提示为 UI 层体验优化；接口层权限由后端 403 兜底，两者语义一致。</p>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
