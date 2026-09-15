'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { ArrowLeft, Save, Send, GitBranch, History, FileText, Lock, Play, Zap } from 'lucide-react'
import { promptsService, type PromptDetail, type PromptVersion, type AuditEntry, type DiffResult, type PromptStatus } from '@/services/prompts'
import { evaluationService } from '@/api/evaluation'
import { WHITELIST_KEYS } from '@/config/promptGroups'
import { useToast } from '@/components/shared/Toast'
import Skeleton from '@/components/shared/Skeleton'
import StatusBadge from '@/components/prompts/StatusBadge'
import StatusPipeline from '@/components/prompts/StatusPipeline'

const INPUT_CLS = 'px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors'
const BTN_PRIMARY = 'px-4 py-2 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors flex items-center gap-1.5 disabled:opacity-50'
const BTN_SECONDARY = 'px-4 py-2 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors flex items-center gap-1.5 disabled:opacity-50'

type Tab = 'editor' | 'versions' | 'audit'

export default function PromptDetailPage() {
  const { key } = useParams<{ key: string }>()
  const decodedKey = decodeURIComponent(key)
  const router = useRouter()
  const toast = useToast()

  const [prompt, setPrompt] = useState<PromptDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<Tab>('editor')

  // Editor state
  const [editTemplate, setEditTemplate] = useState('')
  const [changeNote, setChangeNote] = useState('')
  const [saving, setSaving] = useState(false)

  // Versions state
  const [versions, setVersions] = useState<PromptVersion[]>([])
  const [diffResult, setDiffResult] = useState<DiffResult | null>(null)
  const [diffFrom, setDiffFrom] = useState<number | ''>('')
  const [diffTo, setDiffTo] = useState<number | ''>('')

  // Audit state
  const [auditLog, setAuditLog] = useState<AuditEntry[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [detail, vers, audit] = await Promise.all([
        promptsService.get(decodedKey),
        promptsService.versions(decodedKey),
        promptsService.audit(decodedKey),
      ])
      setPrompt(detail)
      setEditTemplate(detail.template)
      setVersions(vers)
      setAuditLog(audit)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [decodedKey, toast])

  useEffect(() => { load() }, [load])

  const handleSaveDraft = async () => {
    if (!editTemplate.trim()) {
      toast.warning('模板不能为空')
      return
    }
    setSaving(true)
    try {
      const res = await promptsService.createDraft(decodedKey, editTemplate, changeNote)
      toast.success(`草稿 v${res.version} 已保存`)
      setChangeNote('')
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handlePublish = async (version: number) => {
    if (prompt?.is_code_controlled) {
      toast.error('代码控制的 Prompt 不可发布')
      return
    }
    try {
      await promptsService.publish(decodedKey, version)
      toast.success(`v${version} 已发布`)
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '发布失败')
    }
  }

  const handleRollback = async (version: number) => {
    try {
      await promptsService.rollback(decodedKey, version)
      toast.success(`已回滚到 v${version}`)
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '回滚失败')
    }
  }

  const [transitioning, setTransitioning] = useState(false)

  const latestVersion = versions.length > 0 ? versions[0] : null
  const latestStatus = latestVersion?.status ?? null
  const isWhitelisted = WHITELIST_KEYS.includes(decodedKey)

  const handleTransition = async (target: PromptStatus) => {
    if (!latestVersion) return
    setTransitioning(true)
    try {
      await promptsService.transition(decodedKey, latestVersion.version, target)
      toast.success(`状态已更新为「${target}」`)
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '状态转换失败')
    } finally {
      setTransitioning(false)
    }
  }

  const [evalRunning, setEvalRunning] = useState(false)

  const handleRunEval = async () => {
    if (evalRunning) return
    setEvalRunning(true)
    toast.info('评测运行中，完成后会通知你')
    try {
      const result = await evaluationService.runEval('rag')
      if (result.ok) {
        const pct = (result.pass_rate * 100).toFixed(1)
        const top1 = (result.top1_accuracy * 100).toFixed(1)
        if (result.pass_rate >= 0.85) {
          toast.success(`评测通过 — 通过率 ${pct}% · Top-1 ${top1}%`)
          if (latestVersion) {
            try {
              await promptsService.transition(decodedKey, latestVersion.version, 'passed')
              load()
            } catch { /* ignore */ }
          }
        } else {
          toast.warning(`评测未通过 — 通过率 ${pct}% · Top-1 ${top1}%`)
        }
      } else {
        toast.error('评测运行异常')
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '评测运行失败')
    } finally {
      setEvalRunning(false)
    }
  }

  const handleDiff = async () => {
    if (diffFrom === '' || diffTo === '' || diffFrom === diffTo) {
      toast.warning('请选择两个不同的版本')
      return
    }
    try {
      const result = await promptsService.diff(decodedKey, diffFrom as number, diffTo as number)
      setDiffResult(result)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Diff 失败')
    }
  }

  if (loading) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-5xl mx-auto px-6 py-8">
          <Skeleton rows={12} />
        </div>
      </div>
    )
  }

  if (!prompt) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-5xl mx-auto px-6 py-8 text-center">
          <p className="text-sm text-text-muted">Prompt 未找到</p>
          <button onClick={() => router.push('/prompts')} className="text-xs text-accent hover:underline mt-2">返回列表</button>
        </div>
      </div>
    )
  }

  const isReadOnly = prompt.is_code_controlled || prompt.risk_level === 'critical'

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* 头部 */}
        <div className="flex items-center gap-3 mb-6">
          <button onClick={() => router.push('/prompts')} className="p-1.5 rounded-lg hover:bg-black/5 text-text-muted transition-colors">
            <ArrowLeft size={18} />
          </button>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold text-text-primary">{prompt.name}</h1>
              {prompt.is_code_controlled && (
                <span className="flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600">
                  <Lock size={9} />
                  代码控制
                </span>
              )}
            </div>
            <p className="text-xs text-text-muted mt-0.5 font-mono">{prompt.key} · v{prompt.active_version ?? '—'} · {prompt.category}</p>
          </div>
          <div className="flex items-center gap-2">
            {tab === 'editor' && (
              <button
                onClick={() => router.push(`/prompts/${encodeURIComponent(decodedKey)}/playground`)}
                className={BTN_SECONDARY}
              >
                <Play size={14} />
                Playground
              </button>
            )}
            {decodedKey === 'rag.qa' && (
              <button
                onClick={handleRunEval}
                disabled={evalRunning}
                className={`${BTN_PRIMARY} ${evalRunning ? 'opacity-60' : ''}`}
              >
                <Zap size={14} />
                {evalRunning ? '评测中...' : '运行评测'}
              </button>
            )}
          </div>
        </div>

        {/* CI/CD 流水线 */}
        {isWhitelisted && (
          <div className="mb-6">
            <StatusPipeline
              current={latestStatus}
              onTransition={isWhitelisted && !isReadOnly ? handleTransition : undefined}
              loading={transitioning}
            />
          </div>
        )}

        {/* Tab 栏 */}
        <div className="flex gap-1 mb-6 border-b border-border-subtle">
          {([
            { id: 'editor' as Tab, label: '编辑器', icon: FileText },
            { id: 'versions' as Tab, label: '版本历史', icon: GitBranch },
            { id: 'audit' as Tab, label: '审计日志', icon: History },
          ]).map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-xs font-medium border-b-2 transition-colors ${
                tab === t.id
                  ? 'border-accent text-accent'
                  : 'border-transparent text-text-muted hover:text-text-secondary'
              }`}
            >
              <t.icon size={14} />
              {t.label}
            </button>
          ))}
        </div>

        {/* 编辑器 Tab */}
        {tab === 'editor' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 space-y-4">
              <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
                <label className="text-xs font-medium text-text-secondary mb-2 block">模板内容</label>
                <textarea
                  value={editTemplate}
                  onChange={e => setEditTemplate(e.target.value)}
                  disabled={isReadOnly}
                  rows={24}
                  className="w-full px-3 py-2 text-xs font-mono rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none resize-y disabled:opacity-60 disabled:cursor-not-allowed"
                  placeholder="输入 Prompt 模板..."
                />
                {isReadOnly && (
                  <p className="text-[11px] text-text-muted mt-1.5 flex items-center gap-1">
                    <Lock size={10} />
                    {prompt.is_code_controlled ? '此 Prompt 由代码控制，不可编辑' : 'CRITICAL 级别 Prompt 只读'}
                  </p>
                )}
              </div>
              {!isReadOnly && (
                <div className="bg-surface-base rounded-xl border border-border-subtle p-4 space-y-3">
                  <input
                    type="text"
                    placeholder="变更说明（可选）"
                    value={changeNote}
                    onChange={e => setChangeNote(e.target.value)}
                    className={`${INPUT_CLS} w-full`}
                  />
                  <div className="flex gap-2">
                    <button onClick={handleSaveDraft} disabled={saving} className={BTN_PRIMARY}>
                      <Save size={14} />
                      {saving ? '保存中...' : '保存草稿'}
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* 变量面板 */}
            <div className="space-y-4">
              <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
                <h3 className="text-xs font-medium text-text-secondary mb-3">变量</h3>
                {prompt.variables.length === 0 ? (
                  <p className="text-[11px] text-text-muted">无变量</p>
                ) : (
                  <div className="space-y-2">
                    {prompt.variables.map(v => (
                      <div key={v.name} className="flex items-center gap-2 text-[11px]">
                        <code className="px-1.5 py-0.5 rounded bg-accent/10 text-accent font-mono">{`{${v.name}}`}</code>
                        {v.required && <span className="text-[9px] text-red-500">必填</span>}
                        {v.description && <span className="text-text-muted truncate">{v.description}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {prompt.required_substrings && prompt.required_substrings.length > 0 && (
                <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
                  <h3 className="text-xs font-medium text-text-secondary mb-3">契约约束</h3>
                  <div className="space-y-1">
                    {prompt.required_substrings.map(s => (
                      <code key={s} className="block text-[11px] font-mono text-text-muted">{s}</code>
                    ))}
                  </div>
                </div>
              )}
              <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
                <h3 className="text-xs font-medium text-text-secondary mb-3">信息</h3>
                <dl className="space-y-1.5 text-[11px]">
                  <div className="flex justify-between">
                    <dt className="text-text-muted">风险等级</dt>
                    <dd className="text-text-primary font-medium">{prompt.risk_level.toUpperCase()}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-text-muted">模板引擎</dt>
                    <dd className="text-text-primary">{prompt.template_engine}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-text-muted">当前版本</dt>
                    <dd className="text-text-primary">v{prompt.active_version ?? '—'}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-text-muted">更新时间</dt>
                    <dd className="text-text-primary">{new Date(prompt.updated_at).toLocaleString()}</dd>
                  </div>
                </dl>
              </div>
            </div>
          </div>
        )}

        {/* 版本历史 Tab */}
        {tab === 'versions' && (
          <div className="space-y-4">
            {/* Diff 工具 */}
            <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
              <h3 className="text-xs font-medium text-text-secondary mb-3">版本对比</h3>
              <div className="flex items-center gap-3">
                <select
                  value={diffFrom}
                  onChange={e => setDiffFrom(e.target.value ? Number(e.target.value) : '')}
                  className={INPUT_CLS}
                >
                  <option value="">起始版本</option>
                  {versions.map(v => (
                    <option key={v.version} value={v.version}>v{v.version}</option>
                  ))}
                </select>
                <span className="text-text-muted">→</span>
                <select
                  value={diffTo}
                  onChange={e => setDiffTo(e.target.value ? Number(e.target.value) : '')}
                  className={INPUT_CLS}
                >
                  <option value="">目标版本</option>
                  {versions.map(v => (
                    <option key={v.version} value={v.version}>v{v.version}</option>
                  ))}
                </select>
                <button onClick={handleDiff} className={BTN_SECONDARY}>
                  对比
                </button>
              </div>
            </div>

            {/* Diff 结果 */}
            {diffResult && (
              <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
                <h3 className="text-xs font-medium text-text-secondary mb-3">
                  v{diffResult.from_version} → v{diffResult.to_version}
                </h3>
                <pre className="text-[11px] font-mono overflow-x-auto whitespace-pre-wrap text-text-primary bg-black/[0.02] rounded-lg p-3">
                  {diffResult.diff || '（无差异）'}
                </pre>
              </div>
            )}

            {/* 版本列表 */}
            <div className="bg-surface-base rounded-xl border border-border-subtle">
              {versions.length === 0 ? (
                <p className="text-sm text-text-muted text-center py-8">暂无版本</p>
              ) : (
                <div className="divide-y divide-border-subtle">
                  {versions.map(v => (
                    <div key={v.version} className="flex items-center gap-4 p-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-text-primary">v{v.version}</span>
                          <StatusBadge status={v.status} />
                          {v.version === prompt.active_version && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-accent/10 text-accent">当前</span>
                          )}
                        </div>
                        {v.change_note && (
                          <p className="text-[11px] text-text-muted mt-0.5">{v.change_note}</p>
                        )}
                        <p className="text-[10px] text-text-muted mt-0.5">
                          {v.created_by} · {new Date(v.created_at).toLocaleString()}
                        </p>
                      </div>
                      <div className="flex gap-2">
                        {v.status === 'passed' && !isReadOnly && (
                          <button onClick={() => handlePublish(v.version)} className="text-[11px] px-2.5 py-1 rounded-md bg-accent text-white hover:bg-accent-hover transition-colors">
                            <Send size={11} className="inline mr-1" />
                            发布
                          </button>
                        )}
                        {v.version !== prompt.active_version && !isReadOnly && (
                          <button onClick={() => handleRollback(v.version)} className="text-[11px] px-2.5 py-1 rounded-md border border-border-subtle text-text-secondary hover:text-text-primary transition-colors">
                            回滚
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* 审计日志 Tab */}
        {tab === 'audit' && (
          <div className="bg-surface-base rounded-xl border border-border-subtle">
            {auditLog.length === 0 ? (
              <p className="text-sm text-text-muted text-center py-8">暂无审计记录</p>
            ) : (
              <div className="divide-y divide-border-subtle">
                {auditLog.map(entry => (
                  <div key={entry.id} className="p-4">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-medium text-text-primary">{entry.action}</span>
                      {entry.from_version != null && entry.to_version != null && (
                        <span className="text-[10px] text-text-muted">
                          v{entry.from_version} → v{entry.to_version}
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-text-muted">
                      {entry.actor} ({entry.role}) · {new Date(entry.created_at).toLocaleString()}
                    </p>
                    {entry.detail && Object.keys(entry.detail).length > 0 && (
                      <pre className="text-[10px] font-mono text-text-muted mt-1 bg-black/[0.02] rounded p-2 overflow-x-auto">
                        {JSON.stringify(entry.detail, null, 2)}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
