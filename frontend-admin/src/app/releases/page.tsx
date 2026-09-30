'use client'

/**
 * /releases — 发布与版本中心（治理 M8，2026-09-30）
 *
 * 数据源：GET /api/admin/releases(/latest)（ai.release_records，12 门
 * Release Gate 跑完由 verify_release_gate.py 直写）。本页只读：
 * 当前版本卡 + 历史发布列表（12 门结果展开）+ 回滚判据（最近一条 PASS）。
 */
import { useEffect, useState } from 'react'
import {
  CheckCircle2, ChevronDown, ChevronRight, History, RefreshCw,
  Rocket, RotateCcw, XCircle,
} from 'lucide-react'
import PageHeader from '@/components/layout/PageHeader'
import ErrorState from '@/components/shared/ErrorState'
import {
  getReleaseLatest, getReleases,
  type ReleaseLatestResponse, type ReleaseListResponse, type ReleaseRecord,
} from '@/api/governance'

const GATE_LABEL: Record<string, string> = {
  Gate0_BuildIdentity: '构建身份',
  Gate1_Migration: '迁移一致',
  Gate2_Auth: '认证拒绝',
  Gate3_Tenant: '租户隔离',
  Gate4_Chat: '主链路',
  Gate5_RAG: '知识问答',
  Gate6_SQL: '数据查询',
  Gate7_CS: '客服域',
  Gate8_Travel: '旅游域',
  Gate9_Selection: '选品漏斗',
  Gate10_TaskRuntime: '任务运行时',
  Gate11_Model: '模型治理',
  Gate12_Observability: '可观测',
}

function gateOrder(gates: Record<string, boolean>): string[] {
  return Object.keys(gates).sort((a, b) => {
    const na = Number(a.match(/^Gate(\d+)/)?.[1] ?? 99)
    const nb = Number(b.match(/^Gate(\d+)/)?.[1] ?? 99)
    return na - nb
  })
}

function GatesPills({ gates }: { gates: Record<string, boolean> }) {
  return (
    <div className="flex flex-wrap gap-1">
      {gateOrder(gates).map((g) => (
        <span
          key={g}
          title={`${GATE_LABEL[g] ?? g}：${gates[g] ? 'PASS' : 'FAIL'}`}
          className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${
            gates[g] ? 'bg-emerald-50 text-emerald-700' : 'bg-red-100 text-red-700'
          }`}
        >
          {g.replace(/^Gate(\d+)_.*/, '$1')}
        </span>
      ))}
    </div>
  )
}

function ResultBadge({ result }: { result: 'PASS' | 'FAIL' }) {
  return result === 'PASS' ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[12px] font-medium text-emerald-700">
      <CheckCircle2 size={13} /> PASS
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-[12px] font-medium text-red-700">
      <XCircle size={13} /> FAIL
    </span>
  )
}

function CurrentVersionCard({ latest, last_pass }: ReleaseLatestResponse) {
  if (!latest) {
    return (
      <div className="mb-5 rounded-lg border border-dashed border-black/10 bg-white p-5 text-[13px] text-text-secondary">
        暂无发布记录——release.sh 跑完 12 门 Release Gate 后自动落库（M8）。
      </div>
    )
  }
  const passCount = Object.values(latest.gates).filter(Boolean).length
  const totalCount = Object.keys(latest.gates).length
  const rollbackNeeded = latest.result !== 'PASS'
  return (
    <div
      className={`mb-5 rounded-lg border p-5 ${
        latest.result === 'PASS' ? 'border-black/5 bg-white' : 'border-red-200 bg-red-50/40'
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Rocket size={18} className="text-text-secondary" />
        <span className="font-mono text-[16px] font-semibold text-text-primary">{latest.git_sha}</span>
        <ResultBadge result={latest.result} />
        <span className="text-[12px] text-text-secondary">
          门禁 {passCount}/{totalCount} · 构建 {latest.build_time || '?'} ·{' '}
          {latest.finished_at ?? latest.created_at}
          {latest.operator ? ` · 操作者 ${latest.operator}` : ''}
        </span>
      </div>
      <div className="mt-3">
        <GatesPills gates={latest.gates} />
      </div>
      {rollbackNeeded && last_pass && (
        <div className="mt-3 flex items-start gap-2 rounded-md bg-amber-50 p-3 text-[12px] leading-relaxed text-amber-800">
          <RotateCcw size={14} className="mt-0.5 shrink-0" />
          <span>
            当前发布 FAIL，最近一次 PASS 为{' '}
            <code className="rounded bg-amber-100 px-1 font-mono">{last_pass.git_sha}</code>
            （{last_pass.finished_at ?? last_pass.created_at}）。回滚 = 用该 commit 重建镜像发布
            （<code className="font-mono">git checkout {last_pass.git_sha} && bash scripts/release.sh</code>）。
          </span>
        </div>
      )}
    </div>
  )
}

function HistoryRow({ release }: { release: ReleaseRecord }) {
  const [open, setOpen] = useState(false)
  const passCount = Object.values(release.gates).filter(Boolean).length
  const totalCount = Object.keys(release.gates).length
  return (
    <div className="rounded-lg border border-black/5 bg-white">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left hover:bg-black/[0.02]"
      >
        {open ? <ChevronDown size={14} className="text-text-muted" />
          : <ChevronRight size={14} className="text-text-muted" />}
        <span className="font-mono text-[13px] font-medium text-text-primary">{release.git_sha}</span>
        <ResultBadge result={release.result} />
        <span className="text-[12px] text-text-secondary">
          {passCount}/{totalCount} 门 · {release.finished_at ?? release.created_at}
          {release.operator ? ` · ${release.operator}` : ''}
        </span>
      </button>
      {open && (
        <div className="border-t border-black/5 px-4 py-3">
          <GatesPills gates={release.gates} />
          <table className="mt-3 w-full text-[12px]">
            <tbody>
              {gateOrder(release.gates).map((g) => (
                <tr key={g} className="border-b border-black/[0.04] last:border-0">
                  <td className="w-28 py-1.5 text-text-secondary">{GATE_LABEL[g] ?? g}</td>
                  <td className="w-24 py-1.5">
                    {release.gates[g] ? (
                      <span className="text-emerald-600">PASS</span>
                    ) : (
                      <span className="font-medium text-red-600">FAIL</span>
                    )}
                  </td>
                  <td className="py-1.5 font-mono text-[11px] text-text-muted">
                    {release.gate_details?.[g] ?? release.gate_details?.[g.replace(/^Gate(\d+)_/, 'g$1')] ?? ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function ReleasesPage() {
  const [latestInfo, setLatestInfo] = useState<ReleaseLatestResponse | null>(null)
  const [list, setList] = useState<ReleaseListResponse | null>(null)
  const [resultFilter, setResultFilter] = useState<'' | 'PASS' | 'FAIL'>('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = (filter: '' | 'PASS' | 'FAIL' = resultFilter) => {
    setLoading(true)
    setError(null)
    Promise.all([getReleaseLatest(), getReleases({ result: filter, limit: 50 })])
      .then(([latest, lst]) => {
        setLatestInfo(latest)
        setList(lst)
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load(resultFilter)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resultFilter])

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6 flex items-start justify-between">
        <PageHeader
          title="发布记录"
          desc="12 门 Release Gate 结果落库 · 发布时间轴与回滚判据"
        />
        <button
          onClick={() => load()}
          className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-black/10 px-3 py-1.5 text-[13px] text-text-secondary hover:bg-black/[0.03]"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
        </button>
      </div>

      {error && <ErrorState message={error} onRetry={() => load()} />}

      {!error && latestInfo && <CurrentVersionCard {...latestInfo} />}

      {!error && list && (
        <>
          <div className="mb-3 mt-6 flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-text-primary">
              <History size={15} /> 历史发布
              <span className="font-normal text-text-muted">（共 {list.total} 条）</span>
            </span>
            <div className="ml-auto flex gap-1">
              {([['', '全部'], ['PASS', 'PASS'], ['FAIL', 'FAIL']] as const).map(([v, label]) => (
                <button
                  key={v || 'all'}
                  onClick={() => setResultFilter(v)}
                  className={`rounded-md px-2.5 py-1 text-[12px] ${
                    resultFilter === v
                      ? 'bg-black/[0.06] font-medium text-text-primary'
                      : 'text-text-secondary hover:bg-black/[0.03]'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          {list.releases.length === 0 ? (
            <div className="rounded-lg border border-dashed border-black/10 bg-white p-6 text-center text-[13px] text-text-secondary">
              无匹配记录
            </div>
          ) : (
            <div className="space-y-2">
              {list.releases.map((r) => (
                <HistoryRow key={r.id} release={r} />
              ))}
            </div>
          )}
        </>
      )}

      <p className="mt-5 text-[11px] leading-relaxed text-text-muted">
        落库链路：scripts/release.sh（契约 lock 漂移检测 → 迁移 preflight → 构建 → up → 12 门冒烟）
        → verify_release_gate.py 跑完直写 ai.release_records（落库失败 = 发布无记录 = 违规，rc=1）。
        契约 lock 漂移检测与 CI 接线（tool_quality workflow）同源：gen_tool_contract_lock --check。
      </p>
    </div>
  )
}
