'use client'

/**
 * /security — 安全运营（方案 A 配套运营面，2026-09-16）
 *
 * 三段式：
 *   1. 灰度开关状态（JWT 会话闸 / 敏感守卫 / 网关会话闸）；
 *      后端两枚经 PUT /sys/config/{key} 免重启切换（DB 覆盖 + env 兜底），
 *      网关值属部署层 env，后端返回 null，展示切换指引而非猜测运行值
 *   2. 在线会话（Redis auth:session:*）+ 强制下线（删键，
 *      enforce 下即刻生效；audit 灰度期仅保证凭据链收紧，页面已提示）
 *   3. 敏感端点清单（后端动态扫描 require_*_user Depends + 人工清单合并）
 *
 * 数据源：/api/sys/security/*，后端统一挂 require_admin_user（403 显式降级）。
 * 布局对齐 /observability/traces（2026-09-18）：min-h 容器 + 面包屑 +
 * 左标题右操作头部 + slate 白卡体系。
 * 2026-09-19：30s 静默轮询（在线会话是实时状态）+ 顶部态势摘要条 +
 * 模式切换取消后强制回拉，防受控 select 显示与后端实际值脱节。
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { LogOut, RefreshCw } from 'lucide-react'
import TraceBreadcrumb from '@/components/observability/trace/TraceBreadcrumb'
import {
  forceLogout,
  getSecurityOverview,
  getSessions,
  updateGuardMode,
  type GuardModeInfo,
  type SecurityOverview,
  type SessionRow,
} from '@/api/securityOps'

const MODE_STYLES: Record<string, string> = {
  enforce: 'bg-red-50 text-red-700 border-red-200',
  audit: 'bg-amber-50 text-amber-700 border-amber-200',
  off: 'bg-slate-50 text-slate-500 border-slate-200',
}

function ModeBadge({ mode, fallback = '未知' }: { mode: string | null; fallback?: string }) {
  const key = mode ?? 'off'
  const label = mode ?? fallback
  return (
    <span className={`inline-block rounded border px-2 py-0.5 text-[11px] font-medium ${MODE_STYLES[key] ?? MODE_STYLES.off}`}>
      {label}
    </span>
  )
}

function ModeCard({ info, title, onSwitch, switching }: {
  info: GuardModeInfo;
  title: string;
  onSwitch?: (value: string) => void;
  switching?: boolean;
}) {
  const switchable = Boolean(info.configKey && info.allowed?.length && info.mode !== null)
  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-slate-800">{title}</span>
        <div className="flex items-center gap-1.5">
          {info.source === 'db' && (
            <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">DB 覆盖</span>
          )}
          {switchable ? (
            <select
              value={info.mode ?? ''}
              disabled={switching}
              onChange={(e) => onSwitch?.(e.target.value)}
              className="rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] text-slate-700 disabled:opacity-50"
            >
              {info.allowed!.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          ) : (
            <ModeBadge mode={info.mode} fallback="部署层" />
          )}
        </div>
      </div>
      <p className="text-[11px] leading-relaxed text-slate-500">{info.note}</p>
      {info.source === 'db' && info.updatedBy && (
        <p className="mt-1 text-[10px] text-slate-400">
          最近修改：{info.updatedBy}{info.updatedAt ? ` · ${info.updatedAt.slice(0, 16).replace('T', ' ')}` : ''}
        </p>
      )}
    </div>
  )
}

/** ISO 时间 → "MM-DD HH:mm"（会话台账展示用，均为服务端时间） */
function fmtDateTime(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** User-Agent → "浏览器 · 系统" 简写（解析失败回退原文截断） */
function fmtUserAgent(ua: string): string {
  if (!ua) return '—'
  const browser =
    ua.includes('Edg/') ? 'Edge'
    : ua.includes('Chrome/') ? 'Chrome'
    : ua.includes('Firefox/') ? 'Firefox'
    : ua.includes('Safari/') ? 'Safari'
    : ''
  const os =
    ua.includes('Windows') ? 'Windows'
    : ua.includes('Mac OS') ? 'macOS'
    : ua.includes('Android') ? 'Android'
    : ua.includes('iPhone') || ua.includes('iPad') ? 'iOS'
    : ua.includes('Linux') ? 'Linux'
    : ''
  const label = [browser, os].filter(Boolean).join(' · ')
  return label || ua.slice(0, 24)
}

function fmtNow(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

/** 态势摘要条的单个指标片：warn 时整体转琥珀底色 */
function SummaryChip({ label, warn = false, children }: { label: string; warn?: boolean; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] ${warn ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-slate-200 bg-white text-slate-600'}`}>
      <span>{label}</span>
      {children}
    </span>
  )
}

export default function SecurityPage() {
  const [overview, setOverview] = useState<SecurityOverview | null>(null)
  const [sessions, setSessions] = useState<SessionRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [kicking, setKicking] = useState<string | null>(null)
  const [switching, setSwitching] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [lastUpdated, setLastUpdated] = useState('')

  const load = useCallback(async (silent = false) => {
    // 静默轮询不清旧错误也不动 loading：避免 30s 一次的轮询把用户正在看的错误提示抹掉
    if (!silent) setLoading(true)
    try {
      const [ov, ss] = await Promise.all([getSecurityOverview(), getSessions()])
      setOverview(ov)
      setSessions(ss.sessions)
      setError('')
    } catch (e) {
      setError((e as Error).message || '加载失败')
    } finally {
      if (!silent) setLoading(false)
      setLastUpdated(fmtNow())
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // 30s 静默轮询；页面隐藏时跳过，避免后台标签页空耗请求
  useEffect(() => {
    const timer = setInterval(() => {
      if (!document.hidden) void load(true)
    }, 30_000)
    return () => clearInterval(timer)
  }, [load])

  async function kick(s: SessionRow) {
    if (!window.confirm(`强制下线 ${s.realName || s.username || `用户 ${s.userId}`} 的该设备会话？（撤销会话及全部刷新凭据，立即生效）`)) return
    setKicking(s.sessionId)
    setNotice('')
    try {
      const { revoked } = await forceLogout(s.sessionId)
      setNotice(revoked
        ? `已强制下线 ${s.realName || s.username || `用户 ${s.userId}`}`
        : '会话已不存在（可能已自然过期或已登出）')
      await load(true)
    } catch (e) {
      setError(`强制下线失败: ${(e as Error).message}`)
    } finally {
      setKicking(null)
    }
  }

  async function switchMode(title: string, configKey: string, value: string) {
    const risky = value === 'enforce' || value === 'off'
    if (!window.confirm(
      risky
        ? `确认把「${title}」切到 ${value}？该操作立即影响请求处置行为（${value === 'off' ? '关闭校验' : '开始拦截'}），旧值可在历史中查回。`
        : `确认把「${title}」切到 ${value}？（audit 仅记日志不拦截）`,
    )) {
      // 取消后无任何 state 变化，受控 select 会停留在误选的新值上 → 强制回拉对齐真实状态
      void load(true)
      return
    }
    setSwitching(configKey)
    setNotice('')
    try {
      const { old, new: newVal } = await updateGuardMode(configKey, value)
      setNotice(`已切换 ${configKey}：${old ?? '(env 值)'} → ${newVal}，免重启生效（其他实例 ≤15s）`)
      await load(true)
    } catch (e) {
      setError(`切换失败: ${(e as Error).message}`)
    } finally {
      setSwitching(null)
    }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        <TraceBreadcrumb crumbs={[{ label: '可观测中心', href: '/observability' }, { label: '安全运营' }]} />

        {/* Header：左标题 + 右操作（刷新），同 traces 页布局 */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-800">安全运营</h1>
            <p className="text-xs text-slate-500 mt-0.5">
              JWT 单通道鉴权的运营面：灰度开关（免重启切换）、在线会话、敏感端点清单
            </p>
          </div>
          <div className="flex items-center gap-3">
            {lastUpdated && (
              <span className="text-[11px] text-slate-400">更新于 {lastUpdated} · 30s 自动刷新</span>
            )}
            <button
              onClick={() => void load()}
              disabled={loading}
              className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 bg-white border border-slate-200 rounded-lg px-3 py-1.5 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> 刷新
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
            {error}
          </div>
        )}
        {notice && (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs text-emerald-700">
            {notice}
          </div>
        )}

        {loading ? (
          <div className="bg-white border border-slate-200 rounded-xl py-12 text-center text-sm text-slate-400">加载中…</div>
        ) : !overview ? (
          <div className="bg-white border border-slate-200 rounded-xl py-12 text-center text-sm text-slate-400">无数据</div>
        ) : (
          <div className="space-y-4">
            {/* 态势摘要条：进页面一眼读全局；audit/off 与 Redis 降级视为异常态高亮 */}
            <div className="flex flex-wrap items-center gap-2">
              <SummaryChip label="会话闸（后端）" warn={overview.modes.jwtSessionGuard.mode !== 'enforce'}>
                <ModeBadge mode={overview.modes.jwtSessionGuard.mode} />
              </SummaryChip>
              <SummaryChip label="敏感守卫" warn={overview.modes.sensitiveApiGuard.mode !== 'enforce'}>
                <ModeBadge mode={overview.modes.sensitiveApiGuard.mode} />
              </SummaryChip>
              <SummaryChip label="会话闸（网关）">
                <ModeBadge mode={overview.modes.gatewaySessionCheck.mode} fallback="部署层" />
              </SummaryChip>
              <SummaryChip label="在线会话">
                <span className="tabular-nums">{sessions.length}</span>
              </SummaryChip>
            </div>

            {/* ① 灰度开关（后端两枚可免重启切换；网关属部署层只读展示） */}
            <section>
              <h2 className="mb-3 text-[13px] font-medium text-slate-800">
                灰度开关
                <span className="ml-2 text-[11px] font-normal text-slate-400">后端两枚免重启切换 · 网关值属部署层只读</span>
              </h2>
              <div className="grid gap-3 md:grid-cols-3">
                <ModeCard
                  title="会话闸（后端中间件）"
                  info={overview.modes.jwtSessionGuard}
                  switching={switching === overview.modes.jwtSessionGuard.configKey}
                  onSwitch={(v) => void switchMode('会话闸（后端中间件）', overview.modes.jwtSessionGuard.configKey!, v)}
                />
                <ModeCard
                  title="敏感守卫（统一依赖）"
                  info={overview.modes.sensitiveApiGuard}
                  switching={switching === overview.modes.sensitiveApiGuard.configKey}
                  onSwitch={(v) => void switchMode('敏感守卫（统一依赖）', overview.modes.sensitiveApiGuard.configKey!, v)}
                />
                <ModeCard title="会话闸（APISIX 网关）" info={overview.modes.gatewaySessionCheck} />
              </div>
            </section>

            {/* ② 在线会话 */}
            <section className="bg-white border border-slate-200 rounded-xl p-4">
              <h2 className="mb-3 text-[13px] font-medium text-slate-800">
                在线会话
                <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] tabular-nums text-slate-500">
                  {sessions.length}
                </span>
              </h2>
              {overview.modes.jwtSessionGuard.mode === 'audit' && (
                <p className="mb-2 text-[11px] text-amber-600">
                  会话闸处于 audit 灰度期：强制下线撤销会话后，闸只记日志不拦截（黑名单通道兜底 logout）；切 enforce 后即刻生效。
                </p>
              )}
              {sessions.length === 0 ? (
                <div className="py-8 text-center text-[12px] text-slate-400">
                  暂无在线会话
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-[12px]">
                    <thead>
                      <tr className="border-b border-slate-200 text-left text-xs font-medium text-slate-500">
                        <th className="px-3 py-2.5 font-medium">用户</th>
                        <th className="px-3 py-2.5 font-medium">角色</th>
                        <th className="px-3 py-2.5 font-medium">设备 / 浏览器</th>
                        <th className="px-3 py-2.5 font-medium">IP</th>
                        <th className="px-3 py-2.5 font-medium">登录时间</th>
                        <th className="px-3 py-2.5 font-medium">最近活跃</th>
                        <th className="px-3 py-2.5 font-medium">过期时间</th>
                        <th className="px-3 py-2.5 text-right font-medium">操作</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {sessions.map((s) => (
                        <tr key={s.sessionId} className="hover:bg-slate-50/60">
                          <td className="px-3 py-2">
                            <span className="text-slate-800">{s.realName || s.username || `用户 ${s.userId}`}</span>
                            <span className="ml-2 text-[11px] text-slate-400">#{s.userId}</span>
                          </td>
                          <td className="px-3 py-2 text-slate-500">{s.role ?? '—'}</td>
                          <td className="px-3 py-2 text-slate-500">
                            <span title={s.userAgent || undefined}>{fmtUserAgent(s.userAgent)}</span>
                            {s.device && (
                              <span className="ml-2 font-mono text-[10px] text-slate-400" title={`deviceId: ${s.device}`}>
                                {s.device.slice(0, 8)}…
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-2 font-mono text-[11px] text-slate-500">{s.ip || '—'}</td>
                          <td className="px-3 py-2 tabular-nums text-slate-500">{fmtDateTime(s.createdAt)}</td>
                          <td className="px-3 py-2 tabular-nums text-slate-500">{fmtDateTime(s.lastActiveAt)}</td>
                          <td className="px-3 py-2 tabular-nums text-slate-500">{fmtDateTime(s.expiresAt)}</td>
                          <td className="px-3 py-2 text-right">
                            <button
                              onClick={() => void kick(s)}
                              disabled={kicking === s.sessionId}
                              className="inline-flex items-center gap-1 rounded border border-red-200 px-2 py-1 text-[11px] text-red-600 hover:bg-red-50 disabled:opacity-50"
                            >
                              <LogOut size={12} /> {kicking === s.sessionId ? '处理中…' : '强制下线'}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* ③ 敏感端点清单 */}
            <section className="bg-white border border-slate-200 rounded-xl p-4">
              <h2 className="mb-3 text-[13px] font-medium text-slate-800">
                敏感端点清单
                <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] tabular-nums text-slate-500">
                  {overview.endpoints.length}
                </span>
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-xs font-medium text-slate-500">
                      <th className="px-3 py-2.5 font-medium">路径</th>
                      <th className="px-3 py-2.5 font-medium">方法</th>
                      <th className="px-3 py-2.5 font-medium">守卫</th>
                      <th className="px-3 py-2.5 font-medium">来源</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {overview.endpoints.map((e) => (
                      <tr key={`${e.path}:${e.methods.join(',')}`} className="hover:bg-slate-50/60">
                        <td className="px-3 py-2 font-mono text-[11px] text-slate-800">{e.path}</td>
                        <td className="px-3 py-2 text-slate-500">{e.methods.join(', ')}</td>
                        <td className="px-3 py-2 font-mono text-[11px] text-slate-500">{e.guard}</td>
                        <td className="px-3 py-2">
                          <span className={`rounded px-1.5 py-0.5 text-[10px] ${e.source === 'runtime' ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-400'}`}>
                            {e.source === 'runtime' ? '运行时扫描' : '人工清单'}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[11px] text-slate-400">
                runtime = 以统一守卫为 Depends 的路由（运行时自动扫描）；curated = 内联守卫端点（人工维护）。新增敏感端点请挂统一守卫，自动进清单。
              </p>
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
