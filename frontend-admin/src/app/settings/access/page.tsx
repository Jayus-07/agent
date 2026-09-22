"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  History,
  KeyRound,
  LogOut,
  RefreshCw,
  Search,
  ShieldCheck,
  UserPlus,
  Users,
} from "lucide-react";
import RoleGate from "@/components/auth/RoleGate";
import {
  type CsRole,
  type PlatformRole,
  type RbacAuditItem,
  type RbacDepartment,
  type RbacUser,
  type RbacUserDetail,
  type RbacUserPatch,
  createRbacUser,
  forceLogoutRbacUser,
  getRbacUserDetail,
  listRbacAudit,
  listRbacDepartments,
  listRbacUsers,
  resetRbacUserPassword,
  updateRbacUser,
} from "@/api/rbac";
import { describeApiError } from "@/api/errors";
import { ApiError } from "@/lib/fetcher";

const PAGE_SIZE = 20;
type Tab = "users" | "roles" | "audit";

interface UserDraft {
  platformRole: PlatformRole;
  dept: string;
  csRole: CsRole | null;
  maxConversations: number;
  enabled: boolean;
  accepting: boolean;
}

function draftFor(user: RbacUser): UserDraft {
  return {
    platformRole: user.platformRole,
    dept: user.dept ?? "",
    csRole: user.csAgent?.role ?? null,
    maxConversations: user.csAgent?.maxConversations ?? 10,
    enabled: user.csAgent?.enabled ?? false,
    accepting: user.csAgent?.accepting ?? false,
  };
}

function statusOf(error: unknown): number | undefined {
  if (error instanceof ApiError) return error.status;
  if (error && typeof error === "object") {
    const status = (error as { status?: unknown }).status;
    return typeof status === "number" ? status : undefined;
  }
  return undefined;
}

function readableError(error: unknown, fallback: string): string {
  const descriptor = describeApiError(error);
  return descriptor.cause === error && descriptor.status
    ? descriptor.message
    : fallback;
}

function roleLabel(role: string | null | undefined): string {
  if (role === "admin") return "管理员";
  if (role === "editor") return "编辑者";
  if (role === "viewer") return "查看者";
  if (role === "supervisor") return "主管";
  if (role === "agent") return "坐席";
  return "未绑定";
}

function auditChanges(item: RbacAuditItem): string {
  const changes: string[] = [];
  if (item.oldPlatformRole !== item.newPlatformRole) {
    changes.push(
      `平台角色 ${roleLabel(item.oldPlatformRole)} → ${roleLabel(item.newPlatformRole)}`,
    );
  }
  const labels: Record<string, string> = {
    role: "客服角色",
    maxConversations: "容量",
    enabled: "启用",
    accepting: "可接单",
  };
  for (const [key, value] of Object.entries(item.csChanges ?? {})) {
    if (!(key in labels)) continue;
    const display =
      typeof value === "boolean"
        ? value
          ? "是"
          : "否"
        : key === "role"
          ? roleLabel(String(value))
          : String(value);
    changes.push(`${labels[key]}：${display}`);
  }
  return changes.length ? changes.join("；") : "无权限字段变化";
}

function AccessControlContent() {
  const [tab, setTab] = useState<Tab>("users");
  const [users, setUsers] = useState<RbacUser[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [drafts, setDrafts] = useState<Record<number, UserDraft>>({});
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [loadingAudit, setLoadingAudit] = useState(false);
  const [savingUserId, setSavingUserId] = useState<number | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  const [audit, setAudit] = useState<RbacAuditItem[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditError, setAuditError] = useState<string | null>(null);
  // ── P6 用户生命周期（新建 / 重置密码 / 强制下线 / 详情 / 角色说明）──
  const [showCreate, setShowCreate] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [issuedPassword, setIssuedPassword] = useState<string | null>(null);
  const [detail, setDetail] = useState<RbacUserDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actingUserId, setActingUserId] = useState<number | null>(null);
  // ── P9 部门维护：下拉数据源来自 departments API（禁前端写死部门清单）──
  const [departments, setDepartments] = useState<RbacDepartment[]>([]);
  const [rowNotices, setRowNotices] = useState<Record<number, string>>({});

  useEffect(() => {
    let cancelled = false;
    listRbacDepartments()
      .then((items) => {
        if (!cancelled) setDepartments(items);
      })
      .catch(() => {
        // 部门主数据未就绪/无权限时下拉为空，不阻塞用户管理主功能
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadUsers = useCallback(async () => {
    setLoadingUsers(true);
    setPageError(null);
    try {
      const result = await listRbacUsers({ search, page, pageSize: PAGE_SIZE });
      setUsers(result.items);
      setTotal(result.total);
      setDrafts(
        Object.fromEntries(result.items.map((user) => [user.userId, draftFor(user)])),
      );
      return true;
    } catch (error) {
      setPageError(
        statusOf(error) === 403
          ? "当前账号没有访问控制权限。"
          : readableError(error, "用户列表加载失败，请稍后重试。"),
      );
      return false;
    } finally {
      setLoadingUsers(false);
    }
  }, [page, search]);

  const loadAudit = useCallback(async () => {
    setLoadingAudit(true);
    setAuditError(null);
    try {
      const result = await listRbacAudit({ page: 1, pageSize: 50 });
      setAudit(result.items);
      setAuditTotal(result.total);
    } catch (error) {
      setAuditError(
        statusOf(error) === 403
          ? "当前账号没有查看审计的权限。"
          : readableError(error, "审计记录加载失败，请稍后重试。"),
      );
    } finally {
      setLoadingAudit(false);
    }
  }, []);

  useEffect(() => {
    void loadUsers();
  }, [loadUsers]);

  useEffect(() => {
    if (tab === "audit") void loadAudit();
  }, [loadAudit, tab]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  };

  const setDraft = (userId: number, patch: Partial<UserDraft>) => {
    setDrafts((current) => ({
      ...current,
      [userId]: { ...current[userId], ...patch },
    }));
  };

  const saveUser = async (user: RbacUser) => {
    const draft = drafts[user.userId];
    if (!draft || savingUserId !== null) return;
    setSavingUserId(user.userId);
    setRowErrors((current) => {
      const next = { ...current };
      delete next[user.userId];
      return next;
    });
    setRowNotices((current) => {
      const next = { ...current };
      delete next[user.userId];
      return next;
    });
    const patch: RbacUserPatch = {
      version: user.version,
      platformRole: draft.platformRole,
      dept: draft.dept,
      csRole: draft.csRole,
      maxConversations: draft.maxConversations,
      enabled: draft.enabled,
      accepting: draft.accepting,
    };
    try {
      const updated = await updateRbacUser(user.userId, patch);
      setUsers((current) =>
        current.map((item) =>
          item.userId === user.userId ? { ...item, ...updated } : item,
        ),
      );
      setDrafts((current) => ({ ...current, [user.userId]: draftFor({ ...user, ...updated }) }));
      // 部门/角色是授权属性：平台侧保存即吊销该用户全部会话
      setRowNotices((current) => ({
        ...current,
        [user.userId]: "已保存。部门/角色变更已即时生效：该用户下次访问需重新登录获取新权限。",
      }));
    } catch (error) {
      if (statusOf(error) === 409) {
        const refreshed = await loadUsers();
        setRowErrors((current) => ({
          ...current,
          [user.userId]: refreshed
            ? "保存冲突：该用户已被其他管理员修改，当前行已刷新，请重新确认后再保存。"
            : "保存冲突且刷新失败，请点击刷新后重新确认。",
        }));
      } else {
        setRowErrors((current) => ({
          ...current,
          [user.userId]:
            statusOf(error) === 403
              ? "没有权限保存该用户。"
              : readableError(error, "保存失败，请稍后重试。"),
        }));
      }
    } finally {
      setSavingUserId(null);
    }
  };

  const shownRange = useMemo(() => {
    if (total === 0) return "0 位用户";
    const start = (page - 1) * PAGE_SIZE + 1;
    return `${start}-${Math.min(page * PAGE_SIZE, total)} / ${total}`;
  }, [page, total]);

  // ── P6 用户生命周期动作 ──

  const toggleUserStatus = async (user: RbacUser) => {
    if (actingUserId !== null) return;
    setActingUserId(user.userId);
    setActionError(null);
    try {
      const updated = await updateRbacUser(user.userId, {
        version: user.version,
        status: user.status === 1 ? 0 : 1,
      });
      setUsers((current) =>
        current.map((item) =>
          item.userId === user.userId ? { ...item, ...updated } : item,
        ),
      );
      setDrafts((current) => ({ ...current, [user.userId]: draftFor({ ...user, ...updated }) }));
    } catch (error) {
      setActionError(readableError(error, "账号状态变更失败，请稍后重试。"));
    } finally {
      setActingUserId(null);
    }
  };

  const resetPassword = async (user: RbacUser) => {
    if (actingUserId !== null) return;
    if (!window.confirm(`确认为「${user.realName || user.username}」重置密码？其全部会话将立即失效。`)) return;
    setActingUserId(user.userId);
    setActionError(null);
    try {
      const result = await resetRbacUserPassword(user.userId);
      setIssuedPassword(result.tempPassword);
      await loadUsers();
    } catch (error) {
      setActionError(readableError(error, "密码重置失败，请稍后重试。"));
    } finally {
      setActingUserId(null);
    }
  };

  const forceLogout = async (user: RbacUser) => {
    if (actingUserId !== null) return;
    if (!window.confirm(`确认强制下线「${user.realName || user.username}」的全部会话？`)) return;
    setActingUserId(user.userId);
    setActionError(null);
    try {
      await forceLogoutRbacUser(user.userId);
      await loadUsers();
    } catch (error) {
      setActionError(readableError(error, "强制下线失败，请稍后重试。"));
    } finally {
      setActingUserId(null);
    }
  };

  const openDetail = async (user: RbacUser) => {
    setDetailLoading(true);
    setActionError(null);
    try {
      setDetail(await getRbacUserDetail(user.userId));
    } catch (error) {
      setActionError(readableError(error, "用户详情加载失败，请稍后重试。"));
    } finally {
      setDetailLoading(false);
    }
  };

  const submitCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (creating) return;
    const form = new FormData(event.currentTarget);
    const username = String(form.get("username") || "").trim();
    const realName = String(form.get("realName") || "").trim();
    const dept = String(form.get("dept") || "").trim();
    const email = String(form.get("email") || "").trim();
    const platformRole = String(form.get("platformRole") || "viewer") as PlatformRole;
    setCreating(true);
    setCreateError(null);
    try {
      const result = await createRbacUser({ username, realName, dept, email, platformRole });
      setShowCreate(false);
      setIssuedPassword(result.tempPassword);
      await loadUsers();
    } catch (error) {
      setCreateError(readableError(error, "创建用户失败，请稍后重试。"));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-7xl space-y-5 p-4 sm:p-6 lg:p-8">
      <header className="flex flex-col gap-4 rounded-2xl border border-gray-200 bg-white/90 p-5 shadow-card sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2 text-accent">
            <ShieldCheck size={17} aria-hidden="true" />
            <span className="text-xs font-medium">访问控制</span>
          </div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight text-text-primary">用户与客服权限</h1>
          <p className="mt-1 text-xs leading-5 text-text-secondary">
            只展示当前租户的权限摘要；保存使用版本校验，避免覆盖其他管理员的修改。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3 rounded-xl bg-surface-elevated px-4 py-3 text-xs text-text-secondary">
          <Users size={16} className="text-accent" aria-hidden="true" />
          <span>当前列表 {total} 位用户</span>
          <span className="h-4 w-px bg-gray-200" aria-hidden="true" />
          <span>仅管理员</span>
        </div>
      </header>

      <section className="rounded-2xl border border-gray-200 bg-white/90 shadow-card">
        <div className="flex flex-col gap-3 border-b border-gray-100 px-4 pt-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
          <div className="flex gap-1" role="tablist" aria-label="访问控制内容">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "users"}
              onClick={() => setTab("users")}
              className={`rounded-lg px-3 py-2 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none ${tab === "users" ? "bg-accent/10 text-accent" : "text-text-secondary hover:bg-gray-50"}`}
            >
              用户
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "roles"}
              onClick={() => setTab("roles")}
              className={`rounded-lg px-3 py-2 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none ${tab === "roles" ? "bg-accent/10 text-accent" : "text-text-secondary hover:bg-gray-50"}`}
            >
              角色
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "audit"}
              onClick={() => setTab("audit")}
              className={`flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none ${tab === "audit" ? "bg-accent/10 text-accent" : "text-text-secondary hover:bg-gray-50"}`}
            >
              <History size={14} aria-hidden="true" />
              审计
              {auditTotal > 0 && <span className="text-[10px]">({auditTotal})</span>}
            </button>
          </div>
          {tab === "users" && (
            <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:items-center">
              <form className="flex w-full gap-2 sm:w-auto" onSubmit={submitSearch}>
                <label className="sr-only" htmlFor="rbac-user-search">搜索用户</label>
                <div className="relative min-w-0 flex-1 sm:w-64">
                  <Search size={14} className="pointer-events-none absolute left-3 top-2.5 text-text-muted" aria-hidden="true" />
                  <input
                    id="rbac-user-search"
                    value={searchInput}
                    onChange={(event) => setSearchInput(event.target.value)}
                    placeholder="搜索用户名、姓名或部门"
                    className="h-9 w-full rounded-lg border border-gray-200 bg-surface-elevated pl-9 pr-3 text-xs text-text-primary outline-none placeholder:text-text-muted focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20"
                  />
                </div>
                <button
                  type="submit"
                  className="h-9 rounded-lg bg-accent px-3 text-xs font-medium text-white hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none"
                >
                  搜索
                </button>
              </form>
              <button
                type="button"
                onClick={() => { setShowCreate(true); setCreateError(null); }}
                className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/5 px-3 text-xs font-medium text-accent hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none"
              >
                <UserPlus size={14} aria-hidden="true" />
                新建用户
              </button>
            </div>
          )}
        </div>

        {tab === "users" ? (
          <div className="p-4 sm:p-5">
            {pageError && (
              <div className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2.5 text-xs text-red-700" role="alert">
                <span>{pageError}</span>
                <button
                  type="button"
                  onClick={() => void loadUsers()}
                  className="shrink-0 rounded-md px-2 py-1 font-medium text-red-700 underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  重试
                </button>
              </div>
            )}
            {loadingUsers ? (
              <div className="space-y-3" aria-live="polite" aria-label="正在加载用户列表">
                {[1, 2, 3].map((item) => (
                  <div key={item} className="h-14 animate-pulse rounded-lg bg-surface-elevated motion-reduce:animate-none" />
                ))}
              </div>
            ) : users.length === 0 && !pageError ? (
              <div className="rounded-xl border border-dashed border-gray-200 bg-surface-elevated px-5 py-12 text-center text-xs text-text-secondary">
                {search ? "没有找到匹配的用户。" : "当前租户还没有可管理的用户。"}
              </div>
            ) : (
              <>
                <div className="overflow-x-auto rounded-xl border border-gray-100">
                  <table className="min-w-[980px] w-full border-collapse text-left text-xs">
                    <caption className="sr-only">用户与客服权限列表</caption>
                    <thead className="bg-surface-elevated text-[11px] font-medium text-text-secondary">
                      <tr>
                        <th className="px-3 py-3">用户</th>
                        <th className="px-3 py-3">平台角色</th>
                        <th className="px-3 py-3">客服角色</th>
                        <th className="px-3 py-3">容量</th>
                        <th className="px-3 py-3">客服状态</th>
                        <th className="px-3 py-3">会话</th>
                        <th className="px-3 py-3 text-right">操作</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {users.map((user) => {
                        const draft = drafts[user.userId] ?? draftFor(user);
                        const saving = savingUserId === user.userId;
                        return (
                          <tr key={user.userId} className="align-top text-text-primary">
                            <td className="px-3 py-3">
                              <div className="font-medium">{user.realName || user.username}</div>
                              <div className="mt-0.5 text-[11px] text-text-muted">{user.username} · {user.dept || "未填写部门"}</div>
                              <div className="mt-1 text-[10px] text-text-muted">账号{user.status === 1 ? "已启用" : "已停用"}</div>
                            </td>
                            <td className="px-3 py-3">
                              <label className="sr-only" htmlFor={`platform-role-${user.userId}`}>平台角色</label>
                              <select
                                id={`platform-role-${user.userId}`}
                                value={draft.platformRole}
                                onChange={(event) => setDraft(user.userId, { platformRole: event.target.value as PlatformRole })}
                                className="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20"
                              >
                                <option value="viewer">查看者</option>
                                <option value="editor">编辑者</option>
                                <option value="admin">管理员</option>
                              </select>
                              <label className="sr-only" htmlFor={`user-dept-${user.userId}`}>部门</label>
                              <select
                                id={`user-dept-${user.userId}`}
                                value={draft.dept}
                                onChange={(event) => setDraft(user.userId, { dept: event.target.value })}
                                className="mt-1.5 block rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20"
                              >
                                <option value="">未分配部门</option>
                                {departments.map((d) => (
                                  <option key={d.code} value={d.code}>{d.name}</option>
                                ))}
                              </select>
                            </td>
                            <td className="px-3 py-3">
                              <label className="sr-only" htmlFor={`cs-role-${user.userId}`}>客服角色</label>
                              <select
                                id={`cs-role-${user.userId}`}
                                value={draft.csRole ?? ""}
                                onChange={(event) => setDraft(user.userId, { csRole: (event.target.value || null) as CsRole | null })}
                                className="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20"
                              >
                                <option value="">未绑定</option>
                                <option value="agent">坐席</option>
                                <option value="supervisor">主管</option>
                              </select>
                            </td>
                            <td className="px-3 py-3">
                              <label className="sr-only" htmlFor={`capacity-${user.userId}`}>最大会话数</label>
                              <input
                                id={`capacity-${user.userId}`}
                                type="number"
                                min={1}
                                max={1000}
                                value={draft.maxConversations}
                                disabled={!draft.csRole}
                                onChange={(event) => setDraft(user.userId, { maxConversations: Math.max(1, Number(event.target.value) || 1) })}
                                className="w-20 rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs outline-none disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-text-muted focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20"
                              />
                            </td>
                            <td className="space-y-2 px-3 py-3">
                              <label className="flex items-center gap-2 text-xs text-text-secondary">
                                <input
                                  type="checkbox"
                                  checked={draft.enabled}
                                  disabled={!draft.csRole}
                                  onChange={(event) => setDraft(user.userId, { enabled: event.target.checked })}
                                  className="h-3.5 w-3.5 rounded border-gray-300 text-accent accent-accent focus-visible:ring-2 focus-visible:ring-accent/30"
                                />
                                启用
                              </label>
                              <label className="flex items-center gap-2 text-xs text-text-secondary">
                                <input
                                  type="checkbox"
                                  checked={draft.accepting}
                                  disabled={!draft.csRole || !draft.enabled}
                                  onChange={(event) => setDraft(user.userId, { accepting: event.target.checked })}
                                  className="h-3.5 w-3.5 rounded border-gray-300 text-accent accent-accent focus-visible:ring-2 focus-visible:ring-accent/30"
                                />
                                可接单
                              </label>
                            </td>
                            <td className="px-3 py-3 text-text-secondary">{user.sessionCount}</td>
                            <td className="px-3 py-3 text-right">
                              <div className="flex flex-wrap items-center justify-end gap-1.5">
                                <button
                                  type="button"
                                  disabled={actingUserId === user.userId}
                                  onClick={() => void saveUser(user)}
                                  className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none"
                                >
                                  {saving ? <RefreshCw size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Check size={13} aria-hidden="true" />}
                                  {saving ? "保存中" : "保存"}
                                </button>
                                <button
                                  type="button"
                                  disabled={actingUserId === user.userId}
                                  onClick={() => void toggleUserStatus(user)}
                                  className="rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs text-text-secondary hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none"
                                >
                                  {user.status === 1 ? "禁用" : "启用"}
                                </button>
                                <button
                                  type="button"
                                  disabled={actingUserId === user.userId}
                                  onClick={() => void resetPassword(user)}
                                  className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs text-text-secondary hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none"
                                >
                                  <KeyRound size={12} aria-hidden="true" />
                                  重置密码
                                </button>
                                <button
                                  type="button"
                                  disabled={actingUserId === user.userId}
                                  onClick={() => void forceLogout(user)}
                                  className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs text-text-secondary hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none"
                                >
                                  <LogOut size={12} aria-hidden="true" />
                                  强制下线
                                </button>
                                <button
                                  type="button"
                                  disabled={detailLoading}
                                  onClick={() => void openDetail(user)}
                                  className="rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs text-text-secondary hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none"
                                >
                                  详情
                                </button>
                              </div>
                              {(rowErrors[user.userId] || actionError) && (
                                <p className="mt-2 max-w-56 text-left text-[11px] leading-4 text-red-600" role="alert">
                                  {rowErrors[user.userId] || actionError}
                                </p>
                              )}
                              {rowNotices[user.userId] && !rowErrors[user.userId] && (
                                <p className="mt-2 max-w-56 text-left text-[11px] leading-4 text-emerald-700" role="status">
                                  {rowNotices[user.userId]}
                                </p>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="mt-4 flex flex-col gap-2 text-[11px] text-text-secondary sm:flex-row sm:items-center sm:justify-between">
                  <span>{shownRange}</span>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      aria-label="上一页"
                      disabled={page <= 1 || loadingUsers}
                      onClick={() => setPage((value) => Math.max(1, value - 1))}
                      className="rounded-lg border border-gray-200 p-1.5 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    >
                      <ChevronLeft size={14} aria-hidden="true" />
                    </button>
                    <span>第 {page} / {totalPages} 页</span>
                    <button
                      type="button"
                      aria-label="下一页"
                      disabled={page >= totalPages || loadingUsers}
                      onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
                      className="rounded-lg border border-gray-200 p-1.5 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    >
                      <ChevronRight size={14} aria-hidden="true" />
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        ) : tab === "roles" ? (
          <div className="p-4 sm:p-5">
            <div className="overflow-x-auto rounded-xl border border-gray-100">
              <table className="min-w-[720px] w-full border-collapse text-left text-xs">
                <caption className="sr-only">平台与客服角色说明（只读）</caption>
                <thead className="bg-surface-elevated text-[11px] font-medium text-text-secondary">
                  <tr>
                    <th className="px-3 py-3">角色</th>
                    <th className="px-3 py-3">类别</th>
                    <th className="px-3 py-3">说明</th>
                    <th className="px-3 py-3">权限范围</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {[
                    { role: "admin", category: "平台角色", desc: "管理员", scope: "全部功能：用户与客服档案管理、模型与供应商、工具审批、访问控制及全部读写" },
                    { role: "editor", category: "平台角色", desc: "编辑者", scope: "知识库运营（入库/复核/词库）、业务分析读写；无用户管理与审批权" },
                    { role: "viewer", category: "平台角色", desc: "查看者", scope: "只读访问总览、业务分析与监控面板；写操作由后端 403 兜底" },
                    { role: "supervisor", category: "客服角色", desc: "客服主管", scope: "坐席工作台全部能力 + 工单重派；由管理员在用户页绑定" },
                    { role: "agent", category: "客服角色", desc: "客服坐席", scope: "接单/处理人工会话；容量与接单状态由管理员配置" },
                  ].map((item) => (
                    <tr key={item.role} className="text-text-primary">
                      <td className="px-3 py-3 font-medium">{item.desc}</td>
                      <td className="px-3 py-3 text-text-secondary">{item.category}</td>
                      <td className="px-3 py-3 text-text-secondary">{item.role}</td>
                      <td className="px-3 py-3 text-text-secondary">{item.scope}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-[11px] text-text-muted">
              第一阶段角色为只读展示；角色定义与权限判定在后端 RBAC（403 兜底）。
            </p>
          </div>
        ) : (
          <div className="p-4 sm:p-5">
            {auditError && (
              <div className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2.5 text-xs text-red-700" role="alert">
                <span>{auditError}</span>
                <button
                  type="button"
                  onClick={() => void loadAudit()}
                  className="shrink-0 rounded-md px-2 py-1 font-medium underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  重试
                </button>
              </div>
            )}
            {loadingAudit ? (
              <div className="h-40 animate-pulse rounded-lg bg-surface-elevated motion-reduce:animate-none" aria-label="正在加载审计记录" />
            ) : audit.length === 0 && !auditError ? (
              <div className="rounded-xl border border-dashed border-gray-200 bg-surface-elevated px-5 py-12 text-center text-xs text-text-secondary">
                暂无权限变更审计记录。
              </div>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-gray-100">
                <table className="min-w-[760px] w-full border-collapse text-left text-xs">
                  <caption className="sr-only">访问控制审计记录</caption>
                  <thead className="bg-surface-elevated text-[11px] font-medium text-text-secondary">
                    <tr>
                      <th className="px-3 py-3">时间</th>
                      <th className="px-3 py-3">操作者</th>
                      <th className="px-3 py-3">目标</th>
                      <th className="px-3 py-3">变化</th>
                      <th className="px-3 py-3">结果</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {audit.map((item) => (
                      <tr key={item.id} className="text-text-primary">
                        <td className="whitespace-nowrap px-3 py-3 text-text-secondary">{item.createdAt ? new Date(item.createdAt).toLocaleString("zh-CN") : "—"}</td>
                        <td className="px-3 py-3">{item.operator || item.actor || "系统"}</td>
                        <td className="px-3 py-3">{item.target || `用户 ${item.targetUserId}`}</td>
                        <td className="max-w-md px-3 py-3 text-text-secondary">{auditChanges(item)}</td>
                        <td className="px-3 py-3">
                          <span className={`rounded-full px-2 py-1 text-[10px] ${item.result === "success" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"}`}>
                            {item.result === "success" ? "成功" : item.result || "未知"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </section>

      {/* 临时密码一次性展示（P6.3：创建用户 / 重置密码共用） */}
      {issuedPassword && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="临时密码">
          <div className="w-full max-w-md rounded-2xl border border-gray-200 bg-white p-5 shadow-card">
            <h2 className="text-sm font-semibold text-text-primary">临时密码（仅显示一次）</h2>
            <p className="mt-2 text-xs leading-5 text-text-secondary">
              请立即复制并转交给用户。用户首次登录时必须修改密码，此前无法访问其他功能。
            </p>
            <code className="mt-3 block select-all rounded-lg bg-surface-elevated px-3 py-2.5 font-mono text-sm text-text-primary">
              {issuedPassword}
            </code>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => void navigator.clipboard?.writeText(issuedPassword)}
                className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-text-secondary hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                复制
              </button>
              <button
                type="button"
                onClick={() => setIssuedPassword(null)}
                className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                我已保存
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 新建用户（P6.1） */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="新建用户">
          <div className="w-full max-w-md rounded-2xl border border-gray-200 bg-white p-5 shadow-card">
            <h2 className="text-sm font-semibold text-text-primary">新建用户</h2>
            <p className="mt-1 text-[11px] text-text-muted">
              创建后生成随机临时密码；租户继承当前管理员（多租户规则）。
            </p>
            <form className="mt-4 space-y-3" onSubmit={submitCreate}>
              <div>
                <label htmlFor="create-username" className="text-xs text-text-secondary">用户名（2-20 位字母/数字/_.-）</label>
                <input id="create-username" name="username" required pattern="[A-Za-z0-9_.-]{2,20}" className="mt-1 h-9 w-full rounded-lg border border-gray-200 px-3 text-xs outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20" />
              </div>
              <div>
                <label htmlFor="create-realname" className="text-xs text-text-secondary">姓名</label>
                <input id="create-realname" name="realName" className="mt-1 h-9 w-full rounded-lg border border-gray-200 px-3 text-xs outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20" />
              </div>
              <div>
                <label htmlFor="create-email" className="text-xs text-text-secondary">邮箱</label>
                <input id="create-email" name="email" type="email" className="mt-1 h-9 w-full rounded-lg border border-gray-200 px-3 text-xs outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20" />
              </div>
              <div>
                <label htmlFor="create-dept" className="text-xs text-text-secondary">部门</label>
                <select
                  id="create-dept"
                  name="dept"
                  defaultValue=""
                  className="mt-1 h-9 w-full rounded-lg border border-gray-200 bg-white px-3 text-xs outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20"
                >
                  <option value="">未分配部门</option>
                  {departments.map((d) => (
                    <option key={d.code} value={d.code}>{d.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="create-role" className="text-xs text-text-secondary">平台角色</label>
                <select id="create-role" name="platformRole" defaultValue="viewer" className="mt-1 h-9 w-full rounded-lg border border-gray-200 bg-white px-3 text-xs outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/20">
                  <option value="viewer">查看者</option>
                  <option value="editor">编辑者</option>
                  <option value="admin">管理员</option>
                </select>
              </div>
              {createError && <p className="text-[11px] text-red-600" role="alert">{createError}</p>}
              <div className="flex justify-end gap-2 pt-1">
                <button type="button" onClick={() => setShowCreate(false)} className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-text-secondary hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                  取消
                </button>
                <button type="submit" disabled={creating} className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                  {creating ? "创建中…" : "创建"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 用户详情（P6.1） */}
      {detail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="用户详情">
          <div className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-gray-200 bg-white p-5 shadow-card">
            <h2 className="text-sm font-semibold text-text-primary">用户详情</h2>
            <dl className="mt-3 grid grid-cols-[96px_1fr] gap-x-3 gap-y-2 text-xs">
              {[
                ["用户名", detail.username],
                ["邮箱", detail.email || "—"],
                ["租户", detail.tenantId],
                ["部门", detail.dept || "—"],
                ["平台角色", detail.platformRoles.map(roleLabel).join("、")],
                ["客服角色", detail.csRoles.length ? detail.csRoles.map(roleLabel).join("、") : "未绑定"],
                ["状态", detail.status === 1 ? "已启用" : "已禁用"],
                ["创建时间", detail.createdAt ? new Date(detail.createdAt).toLocaleString("zh-CN") : "—"],
                ["最近登录", detail.lastLoginAt ? new Date(detail.lastLoginAt).toLocaleString("zh-CN") : "—"],
                ["活跃会话", String(detail.activeSessionCount)],
                ["待改密", detail.mustChangePassword ? "是（临时密码）" : "否"],
              ].map(([term, value]) => (
                <div key={term} className="contents">
                  <dt className="text-text-muted">{term}</dt>
                  <dd className="text-text-primary">{value}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-4 flex justify-end">
              <button type="button" onClick={() => setDetail(null)} className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function AccessControlPage() {
  return (
    <RoleGate minRole="admin" pageName="访问控制">
      <AccessControlContent />
    </RoleGate>
  );
}
