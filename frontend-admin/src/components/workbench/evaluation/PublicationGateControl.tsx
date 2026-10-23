'use client'

import { useState } from 'react'
import { evaluationAssets, type EvaluationBinding } from '@/api/evaluation-assets'

export default function PublicationGateControl({ binding }: { binding: EvaluationBinding }) {
  const [policy, setPolicy] = useState<{ binding_id: string; enabled: boolean } | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const selected = policy?.enabled && policy.binding_id === binding.binding_id
  const load = async () => { try { setPolicy((await evaluationAssets.publicationPolicy(binding.kb_id)).policy); setLoaded(true) } catch (e) { setError(e instanceof Error ? e.message : '读取门禁失败') } }
  return <details className="mt-2 text-xs" onToggle={e => { if (e.currentTarget.open && !loaded) void load() }}><summary className="cursor-pointer py-1 text-text-muted">发布门禁设置</summary><div className="mt-2 max-w-72 space-y-2 whitespace-normal rounded-md bg-slate-50 p-3"><p>{!loaded ? '读取中…' : selected ? '此题集已用于上传发布门禁' : policy?.enabled ? '此知识库正在使用其他题集作为门禁' : '此知识库尚未启用评测门禁'}</p><p className="leading-5 text-text-muted">启用后，每次上传会先评测候选内容；不达标则保留旧版本。可能增加模型用量和上传耗时。</p>{error && <p role="alert" className="text-red-700">{error}</p>}<button disabled={!loaded || busy} className={`rounded border border-border-subtle px-3 py-2 ${selected ? 'text-red-700' : 'text-blue-600'}`} onClick={() => void (async () => { setBusy(true); setError(''); try { await evaluationAssets.setPublicationPolicy(binding.kb_id, binding.binding_id, !selected); await load() } catch (e) { setError(e instanceof Error ? e.message : '门禁保存失败') } finally { setBusy(false) } })()}>{busy ? '保存中…' : selected ? '停用此发布门禁' : '使用此题集作为门禁'}</button></div></details>
}
