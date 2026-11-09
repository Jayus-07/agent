/**
 * /tools 动态工具详情抽屉的「AI 语义」（决策 #19）：生成建议 → 编辑草稿 →
 * 审核并入正式描述。（自 page.tsx 拆出，纯移动。）
 *
 * 边界：LLM 仅读工具元数据（不含凭据/URL）；草稿只是候选（独立列，
 * 不生效），审核通过并入后 summary 才进入 Planner/路由 prompt。
 */
import { useState } from 'react'
import { Loader2, ShieldCheck, Sparkles } from 'lucide-react'
import StatusChip, { type StatusTone } from '@/components/layout/StatusChip'
import { AssetActionButton } from '@/components/layout/AssetPageShell'
import { useToast } from '@/components/shared/Toast'
import {
  approveMcpSemantic, saveMcpSemanticDraft, suggestMcpSemantic, type McpTool,
} from '@/api/mcp'
import { errMessage } from '@/api/client'

const SEMANTIC_DRAFT_STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  suggested: { label: 'AI 生成（待审）', tone: 'warn' },
  edited: { label: '人工编辑（待审）', tone: 'warn' },
  applied: { label: '已并入正式描述', tone: 'success' },
}

export function SemanticContractSection({ tool, onDone }: { tool: McpTool; onDone: () => Promise<void> }) {
  const toast = useToast()
  const draft = tool.semantic_draft ?? null
  const approved = tool.semantic_approved ?? null
  const applied = draft?.status === 'applied'
  const [summary, setSummary] = useState(draft?.summary ?? '')
  const [scenariosText, setScenariosText] = useState((draft?.scenarios ?? []).join('\n'))
  const [exclusionsText, setExclusionsText] = useState((draft?.exclusions ?? []).join('\n'))
  const [capabilityHint, setCapabilityHint] = useState(draft?.capability_hint ?? '')
  const [busy, setBusy] = useState<'suggest' | 'save' | 'approve' | ''>('')

  const runSuggest = async () => {
    setBusy('suggest')
    try {
      await suggestMcpSemantic(tool.id)
      toast.success('语义建议已生成（仅草稿，审核后才生效）')
      await onDone()
    } catch (e) {
      toast.error(`生成失败: ${errMessage(e)}`)
    } finally {
      setBusy('')
    }
  }

  const saveDraft = async () => {
    const scenarios = scenariosText.split('\n').map((s) => s.trim()).filter(Boolean)
    const exclusions = exclusionsText.split('\n').map((s) => s.trim()).filter(Boolean)
    const text = summary.trim()
    if (!text) {
      toast.error('用途摘要（summary）不能为空')
      return
    }
    if (text.length > 300) {
      toast.error('用途摘要不能超过 300 字')
      return
    }
    if (scenarios.length > 10 || exclusions.length > 10) {
      toast.error('适用/禁用场景各不超过 10 条')
      return
    }
    if ([...scenarios, ...exclusions].some((s) => s.length > 200)) {
      toast.error('每条场景不能超过 200 字')
      return
    }
    setBusy('save')
    try {
      await saveMcpSemanticDraft(tool.id, {
        summary: text,
        scenarios,
        exclusions,
        capability_hint: capabilityHint.trim() || null,
      })
      toast.success('草稿已保存（仍为候选，审核后才并入描述）')
      await onDone()
    } catch (e) {
      toast.error(`保存失败: ${errMessage(e)}`)
    } finally {
      setBusy('')
    }
  }

  const approveDraft = async () => {
    if (!window.confirm(
      '审核通过后，草稿摘要将并入正式描述并进入 Planner / 路由 prompt。确认并入？',
    )) return
    setBusy('approve')
    try {
      await approveMcpSemantic(tool.id)
      toast.success('语义契约已并入正式描述')
      await onDone()
    } catch (e) {
      toast.error(`并入失败: ${errMessage(e)}`)
    } finally {
      setBusy('')
    }
  }

  const suggestButton = (
    <AssetActionButton disabled={busy !== ''} onClick={() => void runSuggest()}>
      {busy === 'suggest'
        ? <Loader2 size={13} className="animate-spin" />
        : <Sparkles size={13} />}
      {busy === 'suggest' ? '生成中…（LLM 分析约需数十秒）' : (applied || draft) ? '重新生成（覆盖草稿）' : '生成语义建议'}
    </AssetActionButton>
  )

  return (
    <div className="space-y-3 text-[12.5px]">
      <div className="rounded-lg border border-border-subtle bg-surface-base px-3 py-2 text-[11.5px] leading-relaxed text-text-muted">
        AI 仅读取工具元数据（名称 / 描述 / Schema / 示例 / 连接名 / 操作类型）生成建议，
        绝不包含凭据与地址；生成内容只是草稿，需管理员「审核通过并入」才会更新正式描述。
      </div>

      {applied && approved && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-3">
          <div className="mb-1.5 flex flex-wrap items-center gap-2">
            <StatusChip tone="success">已并入正式描述</StatusChip>
            <span className="text-[11px] text-text-muted">
              {approved.approved_at ? new Date(approved.approved_at).toLocaleString() : ''}
              （by {approved.approved_by || '—'}）
            </span>
          </div>
          <div className="text-[12px] leading-relaxed">{approved.summary}</div>
          {(approved.scenarios?.length ?? 0) > 0 && (
            <div className="mt-1.5 text-[11.5px] text-text-secondary">适用：{approved.scenarios.join('；')}</div>
          )}
          {(approved.exclusions?.length ?? 0) > 0 && (
            <div className="mt-1 text-[11.5px] text-text-secondary">禁用：{approved.exclusions.join('；')}</div>
          )}
          {approved.capability_hint && (
            <div className="mt-1 font-mono text-[11px] text-text-muted">capability_hint: {approved.capability_hint}</div>
          )}
        </div>
      )}

      {!applied && draft && (
        <div className="flex flex-wrap items-center gap-2">
          <StatusChip tone={SEMANTIC_DRAFT_STATUS_META[draft.status]?.tone ?? 'neutral'}>
            {SEMANTIC_DRAFT_STATUS_META[draft.status]?.label ?? draft.status}
          </StatusChip>
          {draft.generated_at && (
            <span className="text-[11px] text-text-muted">
              生成于 {new Date(draft.generated_at).toLocaleString()}
              {draft.edited_at ? ` · 编辑于 ${new Date(draft.edited_at).toLocaleString()}` : ''}
            </span>
          )}
        </div>
      )}

      {(!applied || draft) && (
        <div className="grid gap-3 md:grid-cols-2">
          <label className="flex flex-col gap-1 md:col-span-2">
            <span className="text-text-muted">用途摘要（并入正式描述；≤300 字）</span>
            <textarea value={summary} onChange={(e) => setSummary(e.target.value)} rows={2}
              disabled={applied && !draft}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 disabled:opacity-60" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-text-muted">适用场景（每行一条，≤10 条）</span>
            <textarea value={scenariosText} onChange={(e) => setScenariosText(e.target.value)} rows={3}
              disabled={applied && !draft}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 disabled:opacity-60"
              placeholder={'用户询问订单进度\n核对发货状态'} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-text-muted">禁用场景（每行一条，≤10 条）</span>
            <textarea value={exclusionsText} onChange={(e) => setExclusionsText(e.target.value)} rows={3}
              disabled={applied && !draft}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 disabled:opacity-60"
              placeholder={'退款等写操作'} />
          </label>
          <label className="flex flex-col gap-1 md:col-span-2">
            <span className="text-text-muted">建议关联的静态 capability（可留空）</span>
            <input value={capabilityHint} onChange={(e) => setCapabilityHint(e.target.value)}
              disabled={applied && !draft}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 font-mono"
              placeholder="如 order.lookup（不确定请留空）" />
          </label>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {suggestButton}
        {!applied && draft && (
          <AssetActionButton disabled={busy !== ''} onClick={() => void saveDraft()}>
            {busy === 'save' ? <Loader2 size={13} className="animate-spin" /> : null}
            保存草稿
          </AssetActionButton>
        )}
        {!applied && draft && (
          <AssetActionButton tone="primary" disabled={busy !== ''} onClick={() => void approveDraft()}>
            {busy === 'approve' ? <Loader2 size={13} className="animate-spin" /> : <ShieldCheck size={13} />}
            审核通过并入描述
          </AssetActionButton>
        )}
      </div>
    </div>
  )
}
