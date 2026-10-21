"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Building2, Check, RefreshCw, Search, Users } from "lucide-react";
import {
  type RbacAccessContext,
  type RbacUser,
  listRbacUsers,
  updateRbacUser,
} from "@/api/rbac";
import { describeApiError } from "@/api/errors";
import { ApiError } from "@/lib/fetcher";

const PAGE_SIZE = 20;

interface UserDraft {
  realName: string;
  email: string;
  status: number;
}

function apiError(error: unknown): string {
  if (error instanceof ApiError && error.message.trim()) return error.message.trim();
  const result = describeApiError(error);
  return result.cause === error && result.status
    ? result.message : "成员资料保存失败，请稍后重试。";
}

export default function DepartmentManagerAccess({
  context,
}: {
  context: RbacAccessContext;
}) {
  const [department, setDepartment] = useState(context.managedDepartments[0]?.code ?? "");
  const [users, setUsers] = useState<RbacUser[]>([]);
  const [drafts, setDrafts] = useState<Record<number, UserDraft>>({});
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "inactive">("all");
  const [loading, setLoading] = useState(false);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});

  const load = useCallback(async () => {
    if (!department) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listRbacUsers({
        dept: department,
        search,
        status: statusFilter === "all" ? undefined : statusFilter === "active" ? 1 : 0,
        page,
        pageSize: PAGE_SIZE,
      });
      setUsers(result.items);
      setTotal(result.total);
      setDrafts(Object.fromEntries(result.items.map((user) => [user.userId, {
        realName: user.realName,
        email: user.email ?? "",
        status: user.status,
      }])));
    } catch (loadError) {
      setError(apiError(loadError));
    } finally {
      setLoading(false);
    }
  }, [department, page, search, statusFilter]);

  useEffect(() => { void load(); }, [load]);

  const setDraft = (userId: number, patch: Partial<UserDraft>) => {
    setDrafts((current) => ({ ...current, [userId]: { ...current[userId], ...patch } }));
  };

  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  };

  const save = async (user: RbacUser) => {
    const draft = drafts[user.userId];
    if (!draft || savingId !== null) return;
    if (user.status === 1 && draft.status === 0 && !window.confirm(
      `停用「${user.realName || user.username}」？其访问会立即失效。`,
    )) return;
    setSavingId(user.userId);
    setNotice(null);
    setRowErrors((current) => {
      const next = { ...current };
      delete next[user.userId];
      return next;
    });
    try {
      const update = await updateRbacUser(user.userId, {
        version: user.version,
        realName: draft.realName,
        email: draft.email,
        status: draft.status,
      });
      setUsers((current) => current.map((item) => item.userId === user.userId
        ? { ...item, ...update } : item));
      setDrafts((current) => ({
        ...current,
        [user.userId]: {
          realName: update.realName,
          email: update.email ?? "",
          status: update.status,
        },
      }));
      setNotice(`已保存「${user.realName || user.username}」的成员资料。`);
      if (statusFilter !== "all" && draft.status !== user.status) await load();
    } catch (saveError) {
      setRowErrors((current) => ({ ...current, [user.userId]: apiError(saveError) }));
    } finally {
      setSavingId(null);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const departmentName = context.managedDepartments.find((item) => item.code === department)?.name;

  return (
    <div className="mx-auto w-full max-w-7xl space-y-5 p-4 sm:p-6 lg:p-8">
      <header className="flex flex-col gap-4 rounded-2xl border border-gray-200 bg-white/90 p-5 shadow-card lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2 text-accent"><Building2 size={16} aria-hidden="true" /><span className="text-xs font-medium">部门管理</span></div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight text-text-primary">{departmentName || "本部门成员"}</h1>
          <p className="mt-1 text-xs leading-5 text-text-secondary">只显示你负责部门的成员。你可以维护姓名、邮箱和账号状态。</p>
        </div>
        <div className="flex items-center gap-2 rounded-xl bg-surface-elevated px-4 py-3 text-xs text-text-secondary">
          <Users size={15} className="text-accent" aria-hidden="true" />{total} 位成员
        </div>
      </header>

      <section className="rounded-2xl border border-gray-200 bg-white/90 shadow-card">
        <div className="flex flex-col gap-3 border-b border-gray-100 p-4 lg:flex-row lg:items-center lg:justify-between sm:p-5">
          <div className="flex flex-wrap items-center gap-2">
            {context.managedDepartments.length > 1 && <>
              <label className="sr-only" htmlFor="manager-department">管理部门</label>
              <select id="manager-department" value={department} onChange={(event) => { setPage(1); setDepartment(event.target.value); }} className="h-9 rounded-lg border border-gray-200 bg-white px-2.5 text-xs text-text-secondary outline-none focus-visible:border-accent">
                {context.managedDepartments.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}
              </select>
            </>}
            <form className="flex gap-2" onSubmit={submitSearch}>
              <label className="sr-only" htmlFor="manager-user-search">搜索部门成员</label>
              <div className="relative w-56">
                <Search size={14} className="pointer-events-none absolute left-3 top-2.5 text-text-muted" aria-hidden="true" />
                <input id="manager-user-search" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="搜索姓名或用户名" className="h-9 w-full rounded-lg border border-gray-200 bg-white pl-9 pr-3 text-xs outline-none focus-visible:border-accent" />
              </div>
              <button type="submit" className="h-9 rounded-lg bg-accent px-3 text-xs font-medium text-white hover:bg-accent-hover">搜索</button>
            </form>
            <label className="sr-only" htmlFor="manager-user-status">账号状态</label>
            <select id="manager-user-status" value={statusFilter} onChange={(event) => { setPage(1); setStatusFilter(event.target.value as typeof statusFilter); }} className="h-9 rounded-lg border border-gray-200 bg-white px-2.5 text-xs text-text-secondary outline-none focus-visible:border-accent">
              <option value="all">全部账号</option><option value="active">启用中</option><option value="inactive">已停用</option>
            </select>
          </div>
          <button type="button" onClick={() => void load()} disabled={loading} aria-label="刷新成员列表" className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-gray-200 text-text-secondary hover:bg-gray-50 disabled:opacity-50">
            <RefreshCw size={14} className={loading ? "animate-spin motion-reduce:animate-none" : ""} aria-hidden="true" />
          </button>
        </div>

        <div className="p-4 sm:p-5">
          {notice && <div className="mb-3 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-xs text-emerald-800" role="status">{notice}</div>}
          {error && <div className="mb-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">{error}</div>}
          {loading ? <div className="h-40 animate-pulse rounded-xl bg-surface-elevated motion-reduce:animate-none" aria-label="正在加载成员" /> : users.length === 0 ? (
            <div className="rounded-xl border border-dashed border-gray-200 px-5 py-12 text-center text-xs text-text-secondary">{search ? "没有找到匹配的成员。" : "当前部门没有成员。"}</div>
          ) : (
            <>
              <div className="overflow-x-auto rounded-xl border border-gray-100">
                <table className="min-w-[760px] w-full border-collapse text-left text-xs">
                  <caption className="sr-only">本部门成员资料管理</caption>
                  <thead className="bg-surface-elevated text-[11px] font-medium text-text-secondary"><tr><th className="px-3 py-3">用户</th><th className="px-3 py-3">姓名</th><th className="px-3 py-3">邮箱</th><th className="px-3 py-3">账号状态</th><th className="px-3 py-3 text-right">操作</th></tr></thead>
                  <tbody className="divide-y divide-gray-100">
                    {users.map((user) => {
                      const draft = drafts[user.userId] ?? { realName: user.realName, email: user.email ?? "", status: user.status };
                      const busy = savingId === user.userId;
                      return <tr key={user.userId} className="align-top">
                        <td className="px-3 py-3"><div className="font-medium text-text-primary">{user.username}</div><div className="mt-0.5 text-[11px] text-text-muted">{user.dept}</div></td>
                        <td className="px-3 py-3"><label className="sr-only" htmlFor={`manager-name-${user.userId}`}>姓名</label><input id={`manager-name-${user.userId}`} maxLength={100} value={draft.realName} onChange={(event) => setDraft(user.userId, { realName: event.target.value })} className="h-8 w-36 rounded-lg border border-gray-200 px-2 text-xs outline-none focus-visible:border-accent" /></td>
                        <td className="px-3 py-3"><label className="sr-only" htmlFor={`manager-email-${user.userId}`}>邮箱</label><input id={`manager-email-${user.userId}`} type="email" maxLength={255} value={draft.email} onChange={(event) => setDraft(user.userId, { email: event.target.value })} className="h-8 w-48 rounded-lg border border-gray-200 px-2 text-xs outline-none focus-visible:border-accent" /></td>
                        <td className="px-3 py-3"><label className="sr-only" htmlFor={`manager-status-${user.userId}`}>账号状态</label><select id={`manager-status-${user.userId}`} value={draft.status} onChange={(event) => setDraft(user.userId, { status: Number(event.target.value) })} className="h-8 rounded-lg border border-gray-200 bg-white px-2 text-xs outline-none focus-visible:border-accent"><option value={1}>启用</option><option value={0}>停用</option></select></td>
                        <td className="px-3 py-3 text-right"><button type="button" disabled={busy} onClick={() => void save(user)} className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-60">{busy ? <RefreshCw size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Check size={13} aria-hidden="true" />}{busy ? "保存中" : "保存"}</button>{rowErrors[user.userId] && <p className="mt-2 max-w-52 text-left text-[11px] text-red-600" role="alert">{rowErrors[user.userId]}</p>}</td>
                      </tr>;
                    })}
                  </tbody>
                </table>
              </div>
              <div className="mt-4 flex items-center justify-between text-[11px] text-text-secondary"><span>第 {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} 条，共 {total} 位</span><div className="flex items-center gap-2"><button type="button" disabled={page <= 1 || loading} onClick={() => setPage((value) => Math.max(1, value - 1))} className="rounded-lg border border-gray-200 px-2.5 py-1.5 disabled:opacity-40">上一页</button><span>{page} / {totalPages}</span><button type="button" disabled={page >= totalPages || loading} onClick={() => setPage((value) => Math.min(totalPages, value + 1))} className="rounded-lg border border-gray-200 px-2.5 py-1.5 disabled:opacity-40">下一页</button></div></div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
