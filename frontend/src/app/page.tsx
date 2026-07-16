'use client'

import { Database, BookOpen, Brain, TrendingUp, ArrowRight, ArrowUpRight, ArrowDownRight, Activity, Clock, FileText, Zap, CheckCircle2, AlertTriangle } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { DASHBOARD_KPIS, RECENT_ANALYSES, RECENT_UPLOADS, RECENT_REPORTS, AGENT_HEALTH, SYSTEM_EVENTS } from '@/services/mock/dashboard'

const PIPELINE_STAGES = ['数据接入', '数据清洗', '数据资产', 'RAG 检索', 'AI任务', '报告生成']

export default function DashboardPage() {
  const router = useRouter()

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-text-primary tracking-tight">电商智能数据平台</h1>
          <p className="text-sm text-text-muted mt-1.5">数据接入 → 数据治理 → 数据资产 → AI任务 → 智能报告，全链路闭环</p>
        </div>

        {/* KPI Cards with trends */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          {DASHBOARD_KPIS.map(kpi => (
            <div key={kpi.label} className="bg-surface-base rounded-xl border border-border-subtle p-5 hover:shadow-card transition-shadow duration-200">
              <div className="flex items-center justify-between mb-3">
                <span className="text-lg">{kpi.icon}</span>
                <span className={`flex items-center gap-0.5 text-[11px] font-medium ${kpi.trend >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                  {kpi.trend >= 0 ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                  {kpi.trend >= 0 ? '+' : ''}{kpi.trend}{kpi.label === '系统可用' ? '%' : ''}
                </span>
              </div>
              <div className="text-2xl font-bold text-text-primary">{kpi.value}</div>
              <div className="text-[11px] text-text-muted mt-1">{kpi.trendLabel}</div>
            </div>
          ))}
        </div>

        {/* Pipeline + Agent Health row */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
          {/* Pipeline */}
          <div className="md:col-span-2 bg-surface-base rounded-xl border border-border-subtle p-5">
            <h2 className="text-sm font-semibold text-text-primary mb-4">数据处理全链路</h2>
            <div className="flex items-center gap-0 overflow-x-auto pb-2">
              {PIPELINE_STAGES.map((stage, i) => (
                <div key={stage} className="flex items-center shrink-0">
                  <div className="w-24 py-2 rounded-lg text-center text-[11px] font-medium"
                    style={{ backgroundColor: `hsl(${i * 40}, 60%, 95%)`, color: `hsl(${i * 40}, 60%, 35%)` }}>
                    {stage}
                  </div>
                  {i < 5 && <ArrowRight size={12} className="mx-0.5 text-text-muted shrink-0" />}
                </div>
              ))}
            </div>
          </div>

          {/* Agent Health */}
          <div className="bg-surface-base rounded-xl border border-border-subtle p-5">
            <h2 className="text-sm font-semibold text-text-primary mb-3">Agent Health</h2>
            <div className="space-y-2">
              {[
                { label: 'Ollama', ok: AGENT_HEALTH.ollama },
                { label: 'PostgreSQL', ok: AGENT_HEALTH.postgresql },
                { label: 'ChromaDB', ok: AGENT_HEALTH.chromadb },
                { label: 'FastAPI', ok: AGENT_HEALTH.fastapi },
              ].map(s => (
                <div key={s.label} className="flex items-center justify-between text-xs">
                  <span className="text-text-secondary">{s.label}</span>
                  {s.ok ? <CheckCircle2 size={13} className="text-green-500" /> : <AlertTriangle size={13} className="text-red-500" />}
                </div>
              ))}
              <div className="pt-2 border-t border-border-subtle text-[11px] text-text-muted">
                运行时间: {AGENT_HEALTH.uptime}
              </div>
            </div>
          </div>
        </div>

        {/* 3-column grid: Analyses | Uploads | Reports */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
          <RecentSection icon={Brain} title="最近分析" items={RECENT_ANALYSES} onClick={() => router.push('/agent/tasks')} />
          <RecentSection icon={FileText} title="最近上传" items={RECENT_UPLOADS} onClick={() => router.push('/data-source')} />
          <RecentSection icon={Zap} title="最近报告" items={RECENT_REPORTS} onClick={() => router.push('/agent/reports')} />
        </div>

        {/* System Events */}
        <div className="bg-surface-base rounded-xl border border-border-subtle p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-text-primary flex items-center gap-2">
              <Activity size={14} className="text-accent" /> 系统事件
            </h2>
            <span className="text-[10px] text-text-muted">{SYSTEM_EVENTS.length} 条</span>
          </div>
          <div className="space-y-1">
            {SYSTEM_EVENTS.map(e => (
              <div key={e.id} className="flex items-center gap-2 py-1.5 text-xs">
                <span>{e.level === 'info' ? 'ℹ️' : e.level === 'warn' ? '⚠️' : '🔴'}</span>
                <span className="text-text-primary flex-1">{e.message}</span>
                <span className="text-text-muted shrink-0">{e.time}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function RecentSection({ icon: Icon, title, items, onClick }: { icon: any; title: string; items: any[]; onClick: () => void }) {
  return (
    <div className="bg-surface-base rounded-xl border border-border-subtle p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-text-primary flex items-center gap-2">
          <Icon size={14} className="text-accent" /> {title}
        </h2>
        <button onClick={onClick} className="text-[10px] text-accent hover:underline">全部</button>
      </div>
      <div className="space-y-2.5">
        {items.map(item => (
          <div key={item.id} className="group cursor-pointer" onClick={onClick}>
            <div className="text-xs text-text-primary font-medium truncate">{item.title}</div>
            <div className="flex items-center gap-2 mt-0.5 text-[10px] text-text-muted">
              <span className="bg-surface-elevated px-1.5 py-0.5 rounded">{item.type}</span>
              <span className="text-green-500">{item.status}</span>
              <Clock size={10} />
              <span>{item.time}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
