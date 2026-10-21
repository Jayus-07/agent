"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Check, RefreshCw, Search, Users } from "lucide-react";
import {
  type CsRole,
  type CustomerServiceTeamPage,
  type RbacUser,
  listCustomerServiceTeam,
  updateCustomerServiceMember,
} from "@/api/rbac";
import { describeApiError } from "@/api/errors";
import { ApiError } from "@/lib/fetcher";

const PAGE_SIZE = 20;

interface Draft {
  role: CsRole | "";
  capacity: number;
  enabled: boolean;
  accepting: boolean;
}

function draftFor(user: RbacUser & { csAgent: NonNullable<RbacUser["csAgent"]> | null }): Draft {
  return {
    role: user.csAgent?.enabled ? user.csAgent.role : "",
    capacity: user.csAgent?.maxConversations ?? 10,
    enabled: user.csAgent?.enabled ?? false,
    accepting: user.csAgent?.accepting ?? false,
  };
}

function errorText(error: unknown): string {
  if (error instanceof ApiError && error.message.trim()) return error.message.trim();
  const description = describeApiError(error);
  return description.cause === error && description.status
    ? description.message : "客服档案更新失败，请稍后重试。";
}

export default function CustomerServiceTeamManagement() {
  const [result, setResult] = useState<CustomerServiceTeamPage | null>(null);
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
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
    setLoading(true);
    setError(null);
    try {
      const next = await listCustomerServiceTeam({
        search,
        status: statusFilter === "all" ? undefined : statusFilter === "active" ? 1 : 0,
        page,
        pageSize: PAGE_SIZE,
      });
      setResult(next);
      setDrafts(Object.fromEntries(next.items.map((user) => [user.userId, draftFor(user)])));
    } catch (loadError) {
      setError(errorText(loadError));
    } finally {
      setLoading(false);
    }
  }, [page, search, statusFilter]);

  useEffect(() => { void load(); }, [load]);

  const items = result?.items ?? [];
  const totalPages = Math.max(1, Math.ceil((result?.total ?? 0) / PAGE_SIZE));
  const memberSummary = useMemo(() => {
    const bound = result?.items.filter((item) => item.csAgent).length ?? 0;
    return `${result?.total ?? 0} 位客服部成员 · 本页 ${bound} 位已配置客服档案`;
  }, [result]);

  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  };

  const setDraft = (userId: number, patch: Partial<Draft>) => {
    setDrafts((current) => ({ ...current, [userId]: { ...current[userId], ...patch } }));
  };

  const save = async (user: (typeof items)[number]) => {
    const draft = drafts[user.userId] ?? draftFor(user);
    if (savingId !== null) return;
    if (user.status !== 1) {
      setRowErrors((current) => ({ ...current, [user.userId]: "请先在用户页启用此账号。" }));
      return;
    }
    if (!draft.role && !user.csAgent) {
      setRowErrors((current) => ({ ...current, [user.userId]: "请选择客服角色后再绑定。" }));
      return;
    }
    setSavingId(user.userId);
    setNotice(null);
    setRowErrors((current) => {
      const next = { ...current };
      delete next[user.userId];
      return next;
    });
    try {
      const update = await updateCustomerServiceMember(user.userId, {
        version: user.version,
        csRole: draft.role || null,
        maxConversations: draft.capacity,
        enabled: draft.role ? draft.enabled : false,
        accepting: draft.role ? draft.accepting : false,
      });
      setResult((current) => current ? {
        ...current,
        items: current.items.map((item) => item.userId === user.userId
          ? { ...item, version: update.version, csAgent: update.csAgent }
          : item),
      } : current);
      setDrafts((current) => ({
        ...current,
        [user.userId]: draftFor({ ...user, version: update.version, csAgent: update.csAgent }),
      }));
      setNotice(`已更新「${user.realName || user.username}」的客服档案。`);
    } catch (saveError) {
      setRowErrors((current) => ({ ...current, [user.userId]: errorText(saveError) }));
    } finally {
      setSavingId(null);
    }
  };

  return (
    <div className="p-4 sm:p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-base font-semibold text-text-primary">客服团队</h2>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-text-secondary">
            仅展示归属启用客服部门的用户。客服角色、容量和接单状态与平台角色分开管理。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <form className="flex gap-2" onSubmit={submitSearch}>
            <label className="sr-only" htmlFor="cs-team-search">搜索客服成员</label>
            <div className="relative w-56">
              <Search size={14} className="pointer-events-none absolute left-3 top-2.5 text-text-muted" aria-hidden="true" />
              <input id="cs-team-search" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="搜索姓名或用户名" className="h-9 w-full rounded-lg border border-gray-200 bg-white pl-9 pr-3 text-xs outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20" />
            </div>
            <button type="submit" className="h-9 rounded-lg bg-accent px-3 text-xs font-medium text-white hover:bg-accent-hover">搜索</button>
          </form>
          <label className="sr-only" htmlFor="cs-team-status">账号状态</label>
          <select id="cs-team-status" value={statusFilter} onChange={(event) => { setPage(1); setStatusFilter(event.target.value as typeof statusFilter); }} className="h-9 rounded-lg border border-gray-200 bg-white px-2.5 text-xs text-text-secondary outline-none focus-visible:border-accent">
            <option value="all">全部账号</option>
            <option value="active">启用中</option>
            <option value="inactive">已停用</option>
          </select>
          <button type="button" onClick={() => void load()} disabled={loading} aria-label="刷新客服团队" className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-gray-200 text-text-secondary hover:bg-gray-50 disabled:opacity-50">
            <RefreshCw size={14} className={loading ? "animate-spin motion-reduce:animate-none" : ""} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-gray-100 bg-surface-elevated px-4 py-3 text-xs text-text-secondary">
        <span className="inline-flex items-center gap-2"><Users size={14} className="text-accent" aria-hidden="true" />{memberSummary}</span>
        <span>客服部门：{result?.department?.name ?? "尚未设置"}</span>
      </div>
      {notice && <div className="mt-3 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-xs text-emerald-800" role="status">{notice}</div>}
      {error && <div className="mt-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">{error}</div>}
      {!error && !loading && !result?.department && (
        <div className="mt-5 rounded-xl border border-dashed border-gray-200 px-5 py-12 text-center text-xs leading-5 text-text-secondary">
          还没有启用的客服部门。请先到“部门”页面将客服部标记为客服部门。
        </div>
      )}
      {result?.department && (
        <>
          {loading ? <div className="mt-4 h-40 animate-pulse rounded-xl bg-surface-elevated motion-reduce:animate-none" aria-label="正在加载客服团队" /> : items.length === 0 ? (
            <div className="mt-4 rounded-xl border border-dashed border-gray-200 px-5 py-10 text-center text-xs text-text-secondary">
              {search ? "没有找到匹配的客服部成员。" : "客服部门还没有成员。先在用户页将用户归属到该部门。"}
            </div>
          ) : (
            <>
              <div className="mt-4 overflow-x-auto rounded-xl border border-gray-100">
                <table className="min-w-[850px] w-full border-collapse text-left text-xs">
                  <caption className="sr-only">客服部门成员和客服档案</caption>
                  <thead className="bg-surface-elevated text-[11px] font-medium text-text-secondary">
                    <tr><th className="px-3 py-3">成员</th><th className="px-3 py-3">客服角色</th><th className="px-3 py-3">容量</th><th className="px-3 py-3">接单设置</th><th className="px-3 py-3 text-right">操作</th></tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {items.map((user) => {
                      const draft = drafts[user.userId] ?? draftFor(user);
                      const busy = savingId === user.userId;
                      return (
                        <tr key={user.userId} className="align-top">
                          <td className="px-3 py-3">
                            <div className="font-medium text-text-primary">{user.realName || user.username}</div>
                            <div className="mt-0.5 text-[11px] text-text-muted">{user.username} · {user.dept}</div>
                            <span className={`mt-1 inline-flex rounded-full px-2 py-0.5 text-[10px] ${user.status === 1 ? "bg-emerald-50 text-emerald-700" : "bg-gray-100 text-gray-600"}`}>{user.status === 1 ? "账号启用" : "账号停用"}</span>
                          </td>
                          <td className="px-3 py-3">
                            <label className="sr-only" htmlFor={`cs-role-${user.userId}`}>客服角色</label>
                            <select id={`cs-role-${user.userId}`} value={draft.role} disabled={user.status !== 1} onChange={(event) => setDraft(user.userId, { role: event.target.value as Draft["role"] })} className="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20 disabled:bg-gray-50">
                              <option value="">未绑定 / 停用（保留历史档案）</option><option value="agent">客服坐席</option><option value="supervisor">客服主管</option>
                            </select>
                          </td>
                          <td className="px-3 py-3">
                            <label className="sr-only" htmlFor={`cs-capacity-${user.userId}`}>最大会话数</label>
                            <input id={`cs-capacity-${user.userId}`} type="number" min={1} max={1000} value={draft.capacity} disabled={!draft.role || user.status !== 1} onChange={(event) => setDraft(user.userId, { capacity: Math.max(1, Number(event.target.value) || 1) })} className="w-24 rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs outline-none disabled:bg-gray-50 focus-visible:border-accent" />
                          </td>
                          <td className="space-y-2 px-3 py-3">
                            <label className="flex items-center gap-2 text-xs text-text-secondary"><input type="checkbox" checked={draft.enabled} disabled={!draft.role || user.status !== 1} onChange={(event) => setDraft(user.userId, { enabled: event.target.checked, accepting: event.target.checked && draft.accepting })} className="h-3.5 w-3.5 accent-accent" />客服档案启用</label>
                            <label className="flex items-center gap-2 text-xs text-text-secondary"><input type="checkbox" checked={draft.accepting} disabled={!draft.role || !draft.enabled || user.status !== 1} onChange={(event) => setDraft(user.userId, { accepting: event.target.checked })} className="h-3.5 w-3.5 accent-accent" />允许接单</label>
                          </td>
                          <td className="px-3 py-3 text-right">
                            <button type="button" disabled={busy} onClick={() => void save(user)} className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-60">
                              {busy ? <RefreshCw size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Check size={13} aria-hidden="true" />}{busy ? "保存中" : "保存"}
                            </button>
                            {rowErrors[user.userId] && <p className="mt-2 max-w-52 text-left text-[11px] leading-4 text-red-600" role="alert">{rowErrors[user.userId]}</p>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="mt-4 flex items-center justify-between text-[11px] text-text-secondary">
                <span>第 {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, result.total)} 条，共 {result.total} 位</span>
                <div className="flex items-center gap-2">
                  <button type="button" disabled={page <= 1 || loading} onClick={() => setPage((value) => Math.max(1, value - 1))} className="rounded-lg border border-gray-200 px-2.5 py-1.5 disabled:opacity-40">上一页</button>
                  <span>{page} / {totalPages}</span>
                  <button type="button" disabled={page >= totalPages || loading} onClick={() => setPage((value) => Math.min(totalPages, value + 1))} className="rounded-lg border border-gray-200 px-2.5 py-1.5 disabled:opacity-40">下一页</button>
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
