'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { ArrowLeft, Play, Eye, Zap, Clock, Hash, Loader2 } from 'lucide-react'
import { promptsService, type PromptDetail, type RenderResult, type PlaygroundResult } from '@/services/prompts'
import { useToast } from '@/components/shared/Toast'
import Skeleton from '@/components/shared/Skeleton'

const INPUT_CLS = 'px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors'
const BTN_PRIMARY = 'px-4 py-2 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors flex items-center gap-1.5 disabled:opacity-50'
const BTN_SECONDARY = 'px-4 py-2 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors flex items-center gap-1.5 disabled:opacity-50'

export default function PlaygroundPage() {
  const { key } = useParams<{ key: string }>()
  const decodedKey = decodeURIComponent(key)
  const router = useRouter()
  const toast = useToast()

  const [prompt, setPrompt] = useState<PromptDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [variables, setVariables] = useState<Record<string, string>>({})
  const [overrideTemplate, setOverrideTemplate] = useState('')
  const [useOverride, setUseOverride] = useState(false)

  const [renderResult, setRenderResult] = useState<RenderResult | null>(null)
  const [playgroundResult, setPlaygroundResult] = useState<PlaygroundResult | null>(null)
  const [rendering, setRendering] = useState(false)
  const [running, setRunning] = useState(false)

  useEffect(() => {
    setLoading(true)
    promptsService.get(decodedKey)
      .then(p => {
        setPrompt(p)
        const vars: Record<string, string> = {}
        for (const v of p.variables) {
          vars[v.name] = ''
        }
        setVariables(vars)
      })
      .catch(e => toast.error(e instanceof Error ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }, [decodedKey, toast])

  const getTemplate = useCallback(() => {
    return useOverride && overrideTemplate.trim() ? overrideTemplate : undefined
  }, [useOverride, overrideTemplate])

  const handleRender = async () => {
    setRendering(true)
    setRenderResult(null)
    try {
      const result = await promptsService.render(decodedKey, variables, getTemplate())
      setRenderResult(result)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '渲染失败')
    } finally {
      setRendering(false)
    }
  }

  const handleRunLLM = async () => {
    setRunning(true)
    setPlaygroundResult(null)
    try {
      const result = await promptsService.playground(decodedKey, variables, getTemplate())
      setPlaygroundResult(result)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'LLM 调用失败')
    } finally {
      setRunning(false)
    }
  }

  if (loading) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-5xl mx-auto px-6 py-8">
          <Skeleton rows={10} />
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

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* 头部 */}
        <div className="flex items-center gap-3 mb-6">
          <button onClick={() => router.push(`/prompts/${encodeURIComponent(decodedKey)}`)} className="p-1.5 rounded-lg hover:bg-black/5 text-text-muted transition-colors">
            <ArrowLeft size={18} />
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="text-lg font-semibold text-text-primary">{prompt.name} — Playground</h1>
            <p className="text-xs text-text-muted mt-0.5 font-mono">{prompt.key} · v{prompt.active_version ?? '—'}</p>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* 左侧：输入 */}
          <div className="space-y-4">
            {/* 变量输入 */}
            <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
              <h3 className="text-xs font-medium text-text-secondary mb-3">变量</h3>
              {prompt.variables.length === 0 ? (
                <p className="text-[11px] text-text-muted">此 Prompt 无变量</p>
              ) : (
                <div className="space-y-3">
                  {prompt.variables.map(v => (
                    <div key={v.name}>
                      <label className="text-[11px] text-text-secondary mb-1 flex items-center gap-1.5">
                        <code className="font-mono">{`{${v.name}}`}</code>
                        {v.required && <span className="text-[9px] text-red-500">必填</span>}
                        {v.description && <span className="text-text-muted">— {v.description}</span>}
                      </label>
                      <textarea
                        value={variables[v.name] || ''}
                        onChange={e => setVariables(prev => ({ ...prev, [v.name]: e.target.value }))}
                        rows={v.name === 'context' || v.name === 'input' || v.name === 'question' ? 4 : 2}
                        className={`${INPUT_CLS} w-full resize-y font-mono`}
                        placeholder={`输入 ${v.name}...`}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 模板覆盖 */}
            <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-xs font-medium text-text-secondary">模板覆盖</h3>
                <label className="flex items-center gap-1.5 text-[11px] text-text-muted cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useOverride}
                    onChange={e => setUseOverride(e.target.checked)}
                    className="rounded border-border-subtle"
                  />
                  启用自定义模板
                </label>
              </div>
              <textarea
                value={overrideTemplate}
                onChange={e => setOverrideTemplate(e.target.value)}
                disabled={!useOverride}
                rows={8}
                className={`${INPUT_CLS} w-full resize-y font-mono disabled:opacity-50 disabled:cursor-not-allowed`}
                placeholder="留空则使用当前活跃版本模板..."
              />
            </div>

            {/* 操作按钮 */}
            <div className="flex gap-3">
              <button onClick={handleRender} disabled={rendering || running} className={BTN_SECONDARY}>
                {rendering ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />}
                渲染预览
              </button>
              <button onClick={handleRunLLM} disabled={rendering || running} className={BTN_PRIMARY}>
                {running ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
                运行 LLM
              </button>
            </div>
          </div>

          {/* 右侧：输出 */}
          <div className="space-y-4">
            {/* 渲染结果 */}
            {renderResult && (
              <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-xs font-medium text-text-secondary">渲染结果</h3>
                  <div className="flex items-center gap-3 text-[10px] text-text-muted">
                    {renderResult.version != null && (
                      <span className="flex items-center gap-0.5">
                        <Hash size={10} />
                        v{renderResult.version}
                      </span>
                    )}
                    <span>来源: {renderResult.source}</span>
                  </div>
                </div>
                <pre className="text-[11px] font-mono text-text-primary bg-black/[0.02] rounded-lg p-3 whitespace-pre-wrap break-words max-h-96 overflow-y-auto">
                  {renderResult.text}
                </pre>
              </div>
            )}

            {/* LLM 结果 */}
            {playgroundResult && (
              <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-xs font-medium text-text-secondary">LLM 输出</h3>
                  <div className="flex items-center gap-3 text-[10px] text-text-muted">
                    <span className="flex items-center gap-0.5">
                      <Clock size={10} />
                      {playgroundResult.latency_ms}ms
                    </span>
                    {playgroundResult.rendered.version != null && (
                      <span className="flex items-center gap-0.5">
                        <Hash size={10} />
                        v{playgroundResult.rendered.version}
                      </span>
                    )}
                  </div>
                </div>
                <pre className="text-[11px] font-mono text-text-primary bg-black/[0.02] rounded-lg p-3 whitespace-pre-wrap break-words max-h-96 overflow-y-auto">
                  {playgroundResult.llm_output}
                </pre>
              </div>
            )}

            {!renderResult && !playgroundResult && (
              <div className="bg-surface-base rounded-xl border border-border-subtle p-8 text-center">
                <Play size={24} className="mx-auto text-text-muted/40 mb-2" />
                <p className="text-xs text-text-muted">填写变量后点击「渲染预览」或「运行 LLM」</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
