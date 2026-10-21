"use client";

import { type FormEvent, useState } from "react";
import { Building2, Plus, RefreshCw } from "lucide-react";
import {
  createRbacDepartment,
  type RbacDepartment,
} from "@/api/rbac";
import { describeApiError } from "@/api/errors";
import { ApiError } from "@/lib/fetcher";

interface DepartmentManagementProps {
  departments: RbacDepartment[];
  loading: boolean;
  error: string | null;
  onRefresh: () => Promise<boolean>;
}

function apiError(error: unknown): string {
  if (error instanceof ApiError && error.message.trim()) {
    return error.message.trim();
  }
  const description = describeApiError(error);
  return description.cause === error && description.status
    ? description.message
    : "部门创建失败，请稍后重试。";
}

export default function DepartmentManagement({
  departments,
  loading,
  error,
  onRefresh,
}: DepartmentManagementProps) {
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (creating) return;
    const form = new FormData(event.currentTarget);
    const code = String(form.get("code") || "").trim().toLowerCase();
    const name = String(form.get("name") || "").trim();
    setCreating(true);
    setCreateError(null);
    setNotice(null);
    try {
      const department = await createRbacDepartment({ code, name });
      const refreshed = await onRefresh();
      setShowCreate(false);
      setNotice(
        refreshed
          ? `已创建「${department.name}」部门。`
          : `已创建「${department.name}」，但部门列表刷新失败，请重试。`,
      );
    } catch (requestError) {
      setCreateError(apiError(requestError));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="p-4 sm:p-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-text-primary">部门目录</h2>
          <p className="mt-1 text-xs leading-5 text-text-secondary">
            部门按当前租户隔离。新增部门可用于账号归属；知识库访问范围仍由知识库配置决定。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="text-xs text-text-muted">{departments.length} 个启用部门</span>
          <button
            type="button"
            onClick={() => void onRefresh()}
            disabled={loading}
            aria-label="刷新部门列表"
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-gray-200 text-text-secondary hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <RefreshCw size={14} className={loading ? "animate-spin motion-reduce:animate-none" : ""} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => { setCreateError(null); setShowCreate(true); }}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-accent px-3 text-xs font-medium text-white hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none"
          >
            <Plus size={14} aria-hidden="true" />
            新建部门
          </button>
        </div>
      </div>

      {notice && (
        <div className="mt-4 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2.5 text-xs text-emerald-800" role="status">
          {notice}
        </div>
      )}
      {error && (
        <div className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2.5 text-xs text-red-700" role="alert">
          <span>{error}</span>
          <button
            type="button"
            onClick={() => void onRefresh()}
            className="shrink-0 rounded-md px-2 py-1 font-medium underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            重试
          </button>
        </div>
      )}

      {loading ? (
        <div className="mt-4 space-y-2" aria-label="正在加载部门列表" aria-live="polite">
          {[1, 2, 3].map((item) => (
            <div key={item} className="h-12 animate-pulse rounded-lg bg-surface-elevated motion-reduce:animate-none" />
          ))}
        </div>
      ) : departments.length === 0 && !error ? (
        <div className="mt-4 rounded-xl border border-dashed border-gray-200 bg-surface-elevated px-5 py-12 text-center">
          <Building2 size={22} className="mx-auto text-text-muted" aria-hidden="true" />
          <p className="mt-3 text-xs font-medium text-text-primary">还没有启用的部门</p>
          <p className="mt-1 text-xs text-text-secondary">新建部门后，可在用户管理中分配给账号。</p>
        </div>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-xl border border-gray-100">
          <table className="min-w-[520px] w-full border-collapse text-left text-xs">
            <caption className="sr-only">当前租户启用的部门列表</caption>
            <thead className="bg-surface-elevated text-[11px] font-medium text-text-secondary">
              <tr>
                <th className="px-3 py-3">部门名称</th>
                <th className="px-3 py-3">部门编码</th>
                <th className="px-3 py-3">状态</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {departments.map((department) => (
                <tr key={department.code} className="text-text-primary">
                  <td className="px-3 py-3 font-medium">{department.name}</td>
                  <td className="px-3 py-3">
                    <code className="rounded bg-surface-elevated px-1.5 py-1 text-[11px] text-text-secondary">{department.code}</code>
                  </td>
                  <td className="px-3 py-3">
                    <span className="rounded-full bg-emerald-50 px-2 py-1 text-[10px] text-emerald-700">启用</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-3 text-[11px] leading-5 text-text-muted">
        部门编码创建后保持不变，避免影响已绑定账号和授权关系。目前支持新增，不提供删除或改名。
      </p>

      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-labelledby="create-department-title">
          <div className="w-full max-w-md rounded-2xl border border-gray-200 bg-white p-5 shadow-card">
            <h2 id="create-department-title" className="text-sm font-semibold text-text-primary">新建部门</h2>
            <p className="mt-1 text-[11px] leading-5 text-text-muted">
              部门创建后立即出现在用户管理的部门选项中。
            </p>
            <form className="mt-4 space-y-3" onSubmit={submit}>
              <div>
                <label htmlFor="department-name" className="text-xs text-text-secondary">部门名称</label>
                <input
                  id="department-name"
                  name="name"
                  required
                  maxLength={100}
                  autoFocus
                  placeholder="例如：市场部"
                  className="mt-1 h-9 w-full rounded-lg border border-gray-200 px-3 text-xs text-text-primary outline-none placeholder:text-text-muted focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20"
                />
              </div>
              <div>
                <label htmlFor="department-code" className="text-xs text-text-secondary">部门编码</label>
                <input
                  id="department-code"
                  name="code"
                  required
                  minLength={1}
                  maxLength={50}
                  pattern="[a-z][a-z0-9_-]{0,49}"
                  autoCapitalize="none"
                  autoComplete="off"
                  placeholder="例如：marketing"
                  className="mt-1 h-9 w-full rounded-lg border border-gray-200 px-3 font-mono text-xs text-text-primary outline-none placeholder:font-sans placeholder:text-text-muted focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20"
                />
                <p className="mt-1 text-[10px] leading-4 text-text-muted">
                  使用小写字母开头，可包含数字、下划线和短横线；编码创建后不可修改。
                </p>
              </div>
              {createError && <p className="text-[11px] text-red-600" role="alert">{createError}</p>}
              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setShowCreate(false)}
                  disabled={creating}
                  className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-text-secondary hover:bg-gray-50 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={creating}
                  className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {creating ? "创建中…" : "创建部门"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
