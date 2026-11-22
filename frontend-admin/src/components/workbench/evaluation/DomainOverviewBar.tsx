'use client'

/**
 * 域质量总览条：每域一张小卡（最近一次测评通过率/时间/状态），
 * 点击切换到该域。数据 = 前端聚合三来源记录（发布评测 / CLI 台账 /
 * V2 运行），每域取时间最新一条；不新增后端端点。
 */

import { useQuery } from '@tanstack/react-query'
import { evaluationService } from '@/api/evaluation'
import { promptsService } from '@/api/prompts'
import { atLeast } from '@/lib/auth'
import { ctl } from './proto/evalUi'
import {
  ALL_DOMAIN,
  EVAL_DOMAINS,
  domainLabel,
  moduleToDomain,
  promptKeyToDomain,
  type DomainFilter,
  type EvalDomain,
} from './domains'

const OVERVIEW_LIMIT = 50

interface DomainLatest {
  passRate: number | null
  time: string | null
  running: boolean
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function pickLatest(current: DomainLatest | undefined, candidate: DomainLatest): DomainLatest {
  if (!current) return candidate
  const currentTime = current.time ? new Date(current.time).getTime() : 0
  const candidateTime = candidate.time ? new Date(candidate.time).getTime() : 0
  return candidateTime >= currentTime ? candidate : current
}

function fmtTime(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? '—' : `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export default function DomainOverviewBar({ domain, onSelectDomain }: { domain: DomainFilter; onSelectDomain: (next: DomainFilter) => void }) {
  const allowed = atLeast('super_admin')
  const overviewQuery = useQuery({
    queryKey: ['admin', 'evaluation-domain-overview'],
    enabled: allowed,
    queryFn: async () => {
      const [releases, ledgerRuns, v2Runs] = await Promise.all([
        promptsService.listAllReleases(OVERVIEW_LIMIT).catch(() => []),
        evaluationService.listRuns(OVERVIEW_LIMIT).catch(() => []),
        evaluationService.listV2Runs(OVERVIEW_LIMIT).catch(() => []),
      ])
      const latest = {} as Record<EvalDomain, DomainLatest | undefined>
      for (const record of releases) {
        const key = promptKeyToDomain(record.prompt_key)
        const passRate = asNumber((record.metrics as Record<string, unknown> | undefined)?.candidate_pass_rate)
        latest[key] = pickLatest(latest[key], {
          passRate,
          time: record.created_at || record.updated_at || null,
          running: !['failed', 'passed', 'approved', 'published', 'rolled_back'].includes(record.status),
        })
      }
      for (const run of ledgerRuns) {
        const key = moduleToDomain(run.module)
        latest[key] = pickLatest(latest[key], {
          passRate: asNumber(run.pass_rate),
          time: run.timestamp || null,
          running: run.status === 'running',
        })
      }
      for (const run of v2Runs) {
        const key = moduleToDomain(run.module)
        const total = asNumber(run.total_cases) ?? 0
        const passed = asNumber(run.passed_cases)
        latest[key] = pickLatest(latest[key], {
          passRate: total > 0 && passed !== null ? passed / total : null,
          time: run.finished_at || run.created_at || null,
          running: !['completed', 'failed', 'cancelled', 'blocked'].includes(run.job_status),
        })
      }
      return latest
    },
    refetchInterval: query => (Object.values(query.state.data ?? {}).some(item => item?.running) ? 5000 : false),
  })

  if (!allowed) return null
  const latest = overviewQuery.data

  return <div className="flex flex-wrap items-stretch gap-2 px-3 pb-1 pt-3" role="group" aria-label="域质量总览">
    {EVAL_DOMAINS.map(item => {
      const info = latest?.[item.key]
      const selected = domain === item.key
      const rate = info?.passRate !== null && info?.passRate !== undefined ? `${(info.passRate * 100).toFixed(1)}%` : null
      return <button
        key={item.key}
        type="button"
        aria-pressed={selected}
        className={`${ctl} flex min-w-36 flex-col items-start gap-0.5 px-3 py-2 text-left ${selected ? 'border-accent' : ''}`}
        onClick={() => onSelectDomain(selected ? ALL_DOMAIN : item.key)}
      >
        <span className="flex items-center gap-1.5 text-xs font-medium text-text-primary">
          {domainLabel(item.key)}
          {info?.running && <span className="inline-block size-1.5 animate-pulse rounded-full bg-sky-500" aria-label="评测运行中" />}
        </span>
        {info
          ? <>
            <span className="text-[11px] text-text-secondary">{rate ?? '—'}</span>
            <span className="text-[10px] text-text-muted">{fmtTime(info.time)}</span>
          </>
          : <span className="text-[11px] text-text-muted">暂无评测</span>}
      </button>
    })}
  </div>
}
