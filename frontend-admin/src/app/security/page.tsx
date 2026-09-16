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
 */
import { useCallback, useEffect, useState } from 'react'
import { LogOut, RefreshCw, ShieldCheck } from 'lucide-react'
import PageHeader from '@/components/layout/PageHeader'
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
  off: 'bg-black/[0.04] text-text-secondary border-black/10',
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
    <div className="rounded-lg border border-black/[0.08] bg-white p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-text-primary">{title}</span>
        <div className="flex items-center gap-1.5">
          {info.source === 'db' && (
            <span className="rounded bg-green-50 px-1.5 py-0.5 text-[10px] text-green-700">DB 覆盖</span>
          )}
          {switchable ? (
            <select
              value={info.mode ?? ''}
              disabled={switching}
              onChange={(e) => onSwitch?.(e.target.value)}
              className="rounded border border-black/15 bg-white px-1.5 py-0.5 text-[11px] text-text-primary disabled:opacity-50"
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
      <p className="text-[11px] leading-relaxed text-text-muted">{info.note}</p>
      {info.source === 'db' && info.updatedBy && (
        <p className="mt-1 text-[10px] text-text-muted">
          最近修改：{info.updatedBy}{info.updatedAt ? ` · ${info.updatedAt.slice(0, 16).replace('T', ' ')}` : ''}
        </p>
      )}
    </div>
  )
}

function fmtTtl(sec: number): string {
  if (sec === -2) return '已失效'
  if (sec === -1) return '不过期'
  if (sec >= 86400) return `${Math.floor(sec / 86400)} 天`
  if (sec >= 3600) return `${Math.floor(sec / 3600)} 时 ${Math.floor((sec % 3600) / 60)} 分`
  if (sec >= 60) return `${Math.floor(sec / 60)} 分`
  return `${sec} 秒`
}

export default function SecurityPage() {
  const [overview, setOverview] = useState<SecurityOverview | null>(null)
  const [sessions, setSessions] = useState<SessionRow[]>([])
  const [redisAvailable, setRedisAvailable] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [kicking, setKicking] = useState<string | null>(null)
  const [switching, setSwitching] = useState<string | null>(null)
  const [notice, setNotice] = useState('')

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    setError('')
    try {
      const [ov, ss] = await Promise.all([getSecurityOverview(), getSessions()])
      setOverview(ov)
      setSessions(ss.sessions)
      setRedisAvailable(ss.redisAvailable)
    } catch (e) {
      setError((e as Error).message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function kick(s: SessionRow) {
    const id = `${s.userId}:${s.jti}`
    if (!window.confirm(`强制下线 ${s.realName || s.username || `用户 ${s.userId}`} 的当前会话？（删会话键，立即生效）`)) return
    setKicking(id)
    setNotice('')
    try {
      const { revoked } = await forceLogout(s.userId, s.jti)
      setNotice(revoked
        ? `已强制下线 ${s.realName || s.username || `用户 ${s.userId}`}`
        : '会话键已不存在（可能已自然过期或已登出）')
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
    )) return
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
    <div>
      <PageHeader
        title="安全运营"
        desc="JWT 单通道鉴权的运营面：灰度开关（免重启切换）、在线会话、敏感端点清单"
      />

      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-[12px] text-red-700">
          {error}
        </div>
      )}
      {notice && (
        <div className="mb-4 rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-[12px] text-green-700">
          {notice}
        </div>
      )}

      {loading ? (
        <div className="py-16 text-center text-[12px] text-text-muted">加载中…</div>
      ) : !overview ? (
        <div className="py-16 text-center text-[12px] text-text-muted">无数据</div>
      ) : (
        <div className="space-y-8">
          {/* ① 灰度开关（后端两枚可免重启切换；网关属部署层只读展示） */}
          <section>
            <h2 className="mb-3 text-[13px] font-semibold text-text-primary">灰度开关</h2>
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
          <section>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-[13px] font-semibold text-text-primary">
                在线会话
                {!redisAvailable && (
                  <span className="ml-2 text-[11px] font-normal text-amber-600">Redis 不可用（降级展示）</span>
                )}
              </h2>
              <button
                onClick={() => void load()}
                className="flex items-center gap-1 rounded border border-black/10 px-2 py-1 text-[11px] text-text-secondary hover:bg-black/[0.03]"
              >
                <RefreshCw size={12} /> 刷新
              </button>
            </div>
            {overview.modes.jwtSessionGuard.mode === 'audit' && (
              <p className="mb-2 text-[11px] text-amber-600">
                会话闸处于 audit 灰度期：强制下线删除会话键后，闸只记日志不拦截（黑名单通道兜底 logout）；切 enforce 后即刻生效。
              </p>
            )}
            {sessions.length === 0 ? (
              <div className="rounded-lg border border-black/[0.08] bg-white py-8 text-center text-[12px] text-text-muted">
                暂无在线会话
              </div>
            ) : (
              <div className="overflow-hidden rounded-lg border border-black/[0.08] bg-white">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="border-b border-black/[0.08] bg-black/[0.02] text-left text-[11px] text-text-muted">
                      <th className="px-4 py-2 font-medium">用户</th>
                      <th className="px-4 py-2 font-medium">角色</th>
                      <th className="px-4 py-2 font-medium">会话标识 (jti)</th>
                      <th className="px-4 py-2 font-medium">剩余有效期</th>
                      <th className="px-4 py-2 text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sessions.map((s) => {
                      const id = `${s.userId}:${s.jti}`
                      return (
                        <tr key={s.key} className="border-b border-black/[0.04] last:border-0">
                          <td className="px-4 py-2">
                            <span className="text-text-primary">{s.realName || s.username || `用户 ${s.userId}`}</span>
                            <span className="ml-2 text-[11px] text-text-muted">#{s.userId}</span>
                          </td>
                          <td className="px-4 py-2 text-text-secondary">{s.role ?? '—'}</td>
                          <td className="px-4 py-2 font-mono text-[11px] text-text-muted">{s.jti.slice(0, 12)}…</td>
                          <td className="px-4 py-2 tabular-nums text-text-secondary">{fmtTtl(s.ttlSeconds)}</td>
                          <td className="px-4 py-2 text-right">
                            <button
                              onClick={() => void kick(s)}
                              disabled={kicking === id}
                              className="inline-flex items-center gap-1 rounded border border-red-200 px-2 py-1 text-[11px] text-red-600 hover:bg-red-50 disabled:opacity-50"
                            >
                              <LogOut size={12} /> {kicking === id ? '处理中…' : '强制下线'}
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* ③ 敏感端点清单 */}
          <section>
            <h2 className="mb-3 flex items-center gap-1.5 text-[13px] font-semibold text-text-primary">
              <ShieldCheck size={14} /> 敏感端点清单（{overview.endpoints.length}）
            </h2>
            <div className="overflow-hidden rounded-lg border border-black/[0.08] bg-white">
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="border-b border-black/[0.08] bg-black/[0.02] text-left text-[11px] text-text-muted">
                    <th className="px-4 py-2 font-medium">路径</th>
                    <th className="px-4 py-2 font-medium">方法</th>
                    <th className="px-4 py-2 font-medium">守卫</th>
                    <th className="px-4 py-2 font-medium">来源</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.endpoints.map((e) => (
                    <tr key={`${e.path}:${e.methods.join(',')}`} className="border-b border-black/[0.04] last:border-0">
                      <td className="px-4 py-2 font-mono text-[11px] text-text-primary">{e.path}</td>
                      <td className="px-4 py-2 text-text-secondary">{e.methods.join(', ')}</td>
                      <td className="px-4 py-2 font-mono text-[11px] text-text-secondary">{e.guard}</td>
                      <td className="px-4 py-2">
                        <span className={`rounded px-1.5 py-0.5 text-[10px] ${e.source === 'runtime' ? 'bg-green-50 text-green-700' : 'bg-black/[0.04] text-text-muted'}`}>
                          {e.source === 'runtime' ? '运行时扫描' : '人工清单'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-[11px] text-text-muted">
              runtime = 以统一守卫为 Depends 的路由（运行时自动扫描）；curated = 内联守卫端点（人工维护）。新增敏感端点请挂统一守卫，自动进清单。
            </p>
          </section>
        </div>
      )}
    </div>
  )
}
