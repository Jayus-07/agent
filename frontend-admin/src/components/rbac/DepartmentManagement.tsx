"use client";

import { type FormEvent, useMemo, useState } from "react";
import { Building2, Check, Pencil, Plus, RefreshCw, Search, X } from "lucide-react";
import {
  createRbacDepartment,
  deactivateRbacDepartment,
  listRbacUsers,
  type RbacDepartment,
  type RbacUser,
  updateRbacDepartment,
} from "@/api/rbac";
import { describeApiError } from "@/api/errors";
import { ApiError } from "@/lib/fetcher";

interface DepartmentManagementProps {
  departments: RbacDepartment[];
  loading: boolean;
  error: string | null;
  onRefresh: () => Promise<boolean>;
}

type DepartmentFilter = "all" | "active" | "inactive";

function apiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.message.trim()) return error.message.trim();
  const description = describeApiError(error);
  return description.cause === error && description.status ? description.message : fallback;
}

export default function DepartmentManagement({
  departments,
  loading,
  error,
  onRefresh,
}: DepartmentManagementProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<DepartmentFilter>("all");
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<RbacDepartment | null>(null);
  const [saving, setSaving] = useState(false);
  const [rowAction, setRowAction] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [managerCandidates, setManagerCandidates] = useState<RbacUser[]>([]);
  const [loadingManagers, setLoadingManagers] = useState(false);
  const [selectedManagerId, setSelectedManagerId] = useState("");

  const activeCount = departments.filter((item) => item.status === 1).length;
  const inactiveCount = departments.length - activeCount;
  const missingVersion = departments.some((item) => item.version === null);
  const missingMemberCount = departments.some((item) => item.memberCount === null);
  const memberCount = departments.every((item) => item.memberCount !== null)
    ? departments.reduce((sum, item) => sum + (item.memberCount ?? 0), 0)
    : null;
  const visibleDepartments = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return departments.filter((department) => {
      const matchesStatus = filter === "all"
        || (filter === "active" && department.status === 1)
        || (filter === "inactive" && department.status === 0);
      const matchesQuery = !needle
        || department.name.toLocaleLowerCase().includes(needle)
        || department.code.toLocaleLowerCase().includes(needle);
      return matchesStatus && matchesQuery;
    });
  }, [departments, filter, query]);

  const openCreate = () => {
    setEditing(null);
    setSelectedManagerId("");
    setManagerCandidates([]);
    setFormError(null);
    setShowForm(true);
  };

  const openEdit = async (department: RbacDepartment) => {
    if (department.version === null) return;
    setEditing(department);
    setSelectedManagerId(department.managerUserId ? String(department.managerUserId) : "");
    setFormError(null);
    setShowForm(true);
    setManagerCandidates([]);
    setLoadingManagers(true);
    try {
      const first = await listRbacUsers({ dept: department.code, status: 1, pageSize: 100 });
      const candidates = [...first.items];
      const pages = Math.ceil(first.total / 100);
      for (let page = 2; page <= pages; page += 1) {
        const result = await listRbacUsers({ dept: department.code, status: 1, page, pageSize: 100 });
        candidates.push(...result.items);
      }
      setManagerCandidates(candidates);
    } catch {
      setFormError("部门成员加载失败，主管候选人暂不可用，请重试。");
    } finally {
      setLoadingManagers(false);
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (saving) return;
    const editingDepartment = editing && editing.version !== null
      ? { ...editing, version: editing.version }
      : null;
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name") || "").trim();
    const departmentType = String(form.get("departmentType") || "general") as RbacDepartment["departmentType"];
    const managerValue = String(form.get("managerUserId") || "");
    if (editing && !editingDepartment) {
      setFormError("当前服务尚未提供部门版本信息，请先更新后端并应用 103 号迁移。");
      return;
    }
    setSaving(true);
    setFormError(null);
    setActionError(null);
    setNotice(null);
    try {
      const department = editingDepartment
        ? await updateRbacDepartment(editingDepartment.code, {
          version: editingDepartment.version,
          name,
          departmentType,
          managerUserId: managerValue ? Number(managerValue) : null,
        })
        : await createRbacDepartment({
          code: String(form.get("code") || "").trim().toLowerCase(),
          name,
          departmentType,
        });
      const refreshed = await onRefresh();
      setShowForm(false);
      setNotice(refreshed
        ? `${editingDepartment ? "已更新" : "已创建"}「${department.name}」部门。`
        : `${editingDepartment ? "已更新" : "已创建"}「${department.name}」，但列表刷新失败，请重试。`);
    } catch (requestError) {
      setFormError(apiError(requestError, editingDepartment ? "部门更新失败，请稍后重试。" : "部门创建失败，请稍后重试。"));
    } finally {
      setSaving(false);
    }
  };

  const toggleStatus = async (department: RbacDepartment) => {
    if (rowAction) return;
    if (department.version === null) {
      setActionError("当前服务尚未提供部门版本信息，请先更新后端并应用 103 号迁移。");
      return;
    }
    if (department.status === 1 && !window.confirm(
      `停用「${department.name}」？停用后不能再分配给新用户，历史记录会保留；之后可以重新启用。`,
    )) return;
    setRowAction(department.code);
    setActionError(null);
    setNotice(null);
    try {
      const updated = department.status === 1
        ? await deactivateRbacDepartment(department.code, department.version)
        : await updateRbacDepartment(department.code, {
          version: department.version,
          status: 1,
        });
      const refreshed = await onRefresh();
      setNotice(refreshed
        ? `「${updated.name}」已${updated.status === 1 ? "启用" : "停用"}。`
        : `「${updated.name}」已更新，但列表刷新失败，请重试。`);
    } catch (requestError) {
      setActionError(apiError(requestError, "部门状态更新失败，请稍后重试。"));
    } finally {
      setRowAction(null);
    }
  };

  return (
    <div className="p-4 sm:p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-base font-semibold text-text-primary">部门目录</h2>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-text-secondary">
            管理当前租户的部门与用户归属。部门编码用于授权关联，创建后固定；停用会保留历史记录。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
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
            onClick={openCreate}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-accent px-3 text-xs font-medium text-white hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <Plus size={14} aria-hidden="true" />
            新建部门
          </button>
        </div>
      </div>

      <div className="mt-5 grid grid-cols-3 divide-x divide-gray-100 rounded-xl border border-gray-100 bg-white">
        <div className="px-3 py-3 sm:px-4">
          <p className="text-[11px] text-text-muted">部门总数</p>
          <p className="mt-1 text-lg font-semibold text-text-primary">{departments.length}</p>
        </div>
        <div className="px-3 py-3 sm:px-4">
          <p className="text-[11px] text-text-muted">启用中</p>
          <p className="mt-1 text-lg font-semibold text-emerald-700">{activeCount}</p>
        </div>
        <div className="px-3 py-3 sm:px-4">
          <p className="text-[11px] text-text-muted">部门归属用户</p>
          <p className="mt-1 text-lg font-semibold text-text-primary">{memberCount ?? "—"}</p>
        </div>
      </div>

      {notice && <div className="mt-4 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2.5 text-xs text-emerald-800" role="status">{notice}</div>}
      {missingVersion && !error && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-5 text-amber-800" role="status">
          当前服务尚未提供部门版本信息，暂时不能编辑或变更状态。更新后端并完成 103 号迁移后即可使用完整维护功能。
        </div>
      )}
      {!missingVersion && missingMemberCount && !error && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-5 text-amber-800" role="status">
          当前服务未提供部门归属人数，暂时不能停用启用中的部门。
        </div>
      )}
      {(error || actionError) && (
        <div className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2.5 text-xs text-red-700" role="alert">
          <span>{error || actionError}</span>
          <button type="button" onClick={() => void onRefresh()} className="shrink-0 rounded-md px-2 py-1 font-medium underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">重试</button>
        </div>
      )}

      <div className="mt-5 flex flex-col gap-3 border-b border-gray-100 pb-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex gap-1" role="group" aria-label="部门状态筛选">
          {([
            ["all", `全部 ${departments.length}`],
            ["active", `启用 ${activeCount}`],
            ["inactive", `停用 ${inactiveCount}`],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
              className={`rounded-md px-2.5 py-1.5 text-[11px] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${filter === value ? "bg-accent/10 text-accent" : "text-text-secondary hover:bg-gray-50"}`}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="relative block w-full lg:w-64">
          <span className="sr-only">搜索部门名称或编码</span>
          <Search size={14} className="pointer-events-none absolute left-3 top-2.5 text-text-muted" aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索名称或编码"
            className="h-9 w-full rounded-lg border border-gray-200 bg-white pl-9 pr-3 text-xs text-text-primary outline-none placeholder:text-text-muted focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20"
          />
        </label>
      </div>

      {loading ? (
        <div className="mt-4 space-y-2" aria-label="正在加载部门列表" aria-live="polite">
          {[1, 2, 3].map((item) => <div key={item} className="h-14 animate-pulse rounded-lg bg-surface-elevated motion-reduce:animate-none" />)}
        </div>
      ) : visibleDepartments.length === 0 && !error ? (
        <div className="mt-4 rounded-xl border border-dashed border-gray-200 bg-surface-elevated px-5 py-12 text-center">
          <Building2 size={22} className="mx-auto text-text-muted" aria-hidden="true" />
          <p className="mt-3 text-xs font-medium text-text-primary">{query ? "没有匹配的部门" : "还没有部门"}</p>
          <p className="mt-1 text-xs text-text-secondary">{query ? "试试其他名称或编码。" : "新建部门后，可在用户管理中分配给账号。"}</p>
        </div>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-xl border border-gray-100">
          <table className="min-w-[700px] w-full border-collapse text-left text-xs">
            <caption className="sr-only">当前租户部门目录</caption>
            <thead className="bg-surface-elevated text-[11px] font-medium text-text-secondary">
              <tr>
                <th className="px-3 py-3 sm:px-4">部门</th>
                <th className="px-3 py-3">编码</th>
                <th className="px-3 py-3">归属用户</th>
                <th className="px-3 py-3">状态</th>
                <th className="px-3 py-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {visibleDepartments.map((department) => {
                const busy = rowAction === department.code;
                return (
                  <tr key={department.code} className="text-text-primary">
                    <td className="px-3 py-3 sm:px-4">
                      <div className="font-medium">{department.name}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        <span className={`rounded-full px-2 py-0.5 text-[10px] ${department.departmentType === "customer_service" ? "bg-violet-50 text-violet-700" : "bg-gray-100 text-gray-600"}`}>
                          {department.departmentType === "customer_service" ? "客服部门" : "普通部门"}
                        </span>
                        {department.managerName && <span className="text-[10px] text-text-muted">主管：{department.managerName}</span>}
                      </div>
                      {department.status === 0 && <div className="mt-0.5 text-[10px] text-text-muted">停用后用户无法再选择此部门</div>}
                    </td>
                    <td className="px-3 py-3"><code className="rounded bg-surface-elevated px-1.5 py-1 text-[11px] text-text-secondary">{department.code}</code></td>
                    <td className="px-3 py-3 text-text-secondary">{department.memberCount === null ? "—" : `${department.memberCount} 人`}</td>
                    <td className="px-3 py-3">
                      <span className={`rounded-full px-2 py-1 text-[10px] ${department.status === 1 ? "bg-emerald-50 text-emerald-700" : "bg-gray-100 text-gray-600"}`}>
                        {department.status === 1 ? "启用" : "停用"}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-right">
                      <div className="inline-flex items-center justify-end gap-1.5">
                        <button
                          type="button"
                          onClick={() => void openEdit(department)}
                          disabled={busy || department.version === null}
                          title={department.version === null ? "当前服务未提供部门版本信息，请更新服务后再编辑" : undefined}
                          className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs text-text-secondary hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                        >
                          <Pencil size={12} aria-hidden="true" /> 编辑
                        </button>
                        <button
                          type="button"
                          onClick={() => void toggleStatus(department)}
                          disabled={busy || department.version === null || (department.status === 1 && (department.memberCount === null || department.memberCount > 0))}
                          title={department.version === null
                            ? "当前服务未提供部门版本信息，请更新服务后再修改"
                            : department.status === 1 && department.memberCount === null
                            ? "当前服务未返回归属人数，请更新服务后再停用"
                            : department.status === 1 && department.memberCount !== null && department.memberCount > 0
                              ? "先将部门成员改派到其他部门，再停用此部门"
                              : undefined}
                          className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${department.status === 1 ? "border-gray-200 text-text-secondary hover:bg-gray-50" : "border-emerald-200 text-emerald-700 hover:bg-emerald-50"}`}
                        >
                          {busy ? <RefreshCw size={12} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : department.status === 1 ? <X size={12} aria-hidden="true" /> : <Check size={12} aria-hidden="true" />}
                          {department.status === 1 ? "停用" : "启用"}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-3 text-[11px] leading-5 text-text-muted">
        部门编码用于知识库授权关联，创建后不可修改。停用会保留审计记录；仍有用户归属时，需先调整成员。
      </p>

      {showForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-labelledby="department-form-title">
          <div className="w-full max-w-md rounded-2xl border border-gray-200 bg-white p-5 shadow-card">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 id="department-form-title" className="text-sm font-semibold text-text-primary">{editing ? "编辑部门" : "新建部门"}</h2>
                <p className="mt-1 text-[11px] leading-5 text-text-muted">{editing ? "仅修改显示名称，部门编码保持不变。" : "新部门会立即出现在用户归属选项中。"}</p>
              </div>
              <button type="button" aria-label="关闭" onClick={() => setShowForm(false)} disabled={saving} className="rounded-md p-1 text-text-muted hover:bg-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"><X size={16} aria-hidden="true" /></button>
            </div>
            <form className="mt-4 space-y-3" onSubmit={submit}>
              <div>
                <label htmlFor="department-name" className="text-xs text-text-secondary">部门名称</label>
                <input
                  id="department-name"
                  name="name"
                  required
                  maxLength={100}
                  autoFocus
                  defaultValue={editing?.name ?? ""}
                  placeholder="例如：市场部"
                  className="mt-1 h-9 w-full rounded-lg border border-gray-200 px-3 text-xs text-text-primary outline-none placeholder:text-text-muted focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20"
                />
              </div>
              <div>
                <label htmlFor="department-type" className="text-xs text-text-secondary">部门类型</label>
                <select
                  id="department-type"
                  name="departmentType"
                  defaultValue={editing?.departmentType ?? "general"}
                  className="mt-1 h-9 w-full rounded-lg border border-gray-200 bg-white px-3 text-xs text-text-primary outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20"
                >
                  <option value="general">普通部门</option>
                  <option value="customer_service">客服部门（每租户仅一个启用）</option>
                </select>
                <p className="mt-1 text-[10px] leading-4 text-text-muted">客服成员管理只读取此类型的启用部门；移除客服类型前需先停用客服档案。</p>
              </div>
              {!editing && (
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
                  <p className="mt-1 text-[10px] leading-4 text-text-muted">小写字母开头，可包含数字、下划线和短横线；编码创建后固定。</p>
                </div>
              )}
              {editing && (
                <div>
                  <label htmlFor="department-manager" className="text-xs text-text-secondary">部门主管</label>
                  <select
                    id="department-manager"
                    name="managerUserId"
                    value={selectedManagerId}
                    onChange={(event) => setSelectedManagerId(event.target.value)}
                    disabled={loadingManagers}
                    className="mt-1 h-9 w-full rounded-lg border border-gray-200 bg-white px-3 text-xs text-text-primary outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20 disabled:bg-gray-50"
                  >
                    <option value="">暂不设置主管</option>
                    {managerCandidates.map((user) => (
                      <option key={user.userId} value={user.userId}>
                        {user.realName || user.username}（{user.username}）
                      </option>
                    ))}
                  </select>
                  <p className="mt-1 text-[10px] leading-4 text-text-muted">
                    仅可选择本部门启用用户；主管能管理本部门成员资料和账号状态，不能修改平台角色或跨部门授权。
                  </p>
                </div>
              )}
              {formError && <p className="text-[11px] text-red-600" role="alert">{formError}</p>}
              <div className="flex justify-end gap-2 pt-1">
                <button type="button" onClick={() => setShowForm(false)} disabled={saving} className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-text-secondary hover:bg-gray-50 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">取消</button>
                <button type="submit" disabled={saving} className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                  {saving && <RefreshCw size={12} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />}
                  {saving ? "保存中…" : editing ? "保存修改" : "创建部门"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
