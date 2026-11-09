/**
 * /tools 动态工具详情抽屉：概览 / Schema / 执行策略（审核+域+授权）/
 * AI 语义 / 调试 / 历史。（自 page.tsx 拆出，纯移动。）
 *
 * 详情缓存（_detailCache）：同 id 重复打开抽屉不重复请求；写操作后由
 * invalidateMcpToolDetailCache 失效并强制拉新，避免展示旧 Schema 与旧治理配置。
 */
import { useCallback, useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import DetailDrawer from '@/components/layout/DetailDrawer'
import StatusChip from '@/components/layout/StatusChip'
import { AssetActionButton } from '@/components/layout/AssetPageShell'
import ErrorState from '@/components/shared/ErrorState'
import Skeleton from '@/components/shared/Skeleton'
import { useToast } from '@/components/shared/Toast'
import { disableMcpTool, getMcpToolDetail, type McpTool } from '@/api/mcp'
import { errMessage } from '@/api/client'
import { DynamicSourceBadge } from './badges'
import { domainLabel, MCP_STATUS_META } from './meta'
import { DebugInvokeForm, invokeSchemaProperties } from './DebugInvokeForm'
import { ReviewPolicyForm } from './ReviewPolicyForm'
import { SemanticContractSection } from './SemanticContractSection'
import { ToolHistorySection } from './ToolHistorySection'

let _detailCache: Record<number, McpTool> = {}
/** 失效详情缓存：重新发现 / 审核 / 停用 / 授权变更后必须调用，
 *  否则抽屉会继续展示旧 Schema 与旧治理配置。 */
function invalidateMcpToolDetailCache(ids?: number[]) {
  if (!ids) {
    _detailCache = {}
    return
  }
  _detailCache = Object.fromEntries(
    Object.entries(_detailCache).filter(([k]) => !ids.includes(Number(k))))
}

async function fetchMcpToolDetailFresh(id: number): Promise<McpTool> {
  invalidateMcpToolDetailCache([id])
  return getMcpToolDetail(id)
}

/** 工具详情抽屉 Tab（AI 语义为真实契约工作区，决策 #19） */
const TOOL_DRAWER_TABS = [
  { id: 'overview', label: '概览' },
  { id: 'schema', label: 'Schema' },
  { id: 'policy', label: '执行策略' },
  { id: 'ai', label: 'AI 语义' },
  { id: 'debug', label: '调试' },
  { id: 'history', label: '历史' },
]

/** 动态工具详情抽屉：概览 / Schema / 执行策略（审核+域+授权）/ 调试 / 历史。 */
export function ToolDrawer({ toolId, onClose, onMutated }: {
  toolId: number
  onClose: () => void
  /** 工具变更（审核/停用/授权）后通知页面刷新列表与统计 */
  onMutated: () => Promise<void>
}) {
  const toast = useToast()
  const [detail, setDetail] = useState<McpTool | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('overview')
  // 提交后用 revision 重挂表单（以服务端返回的最新事实源重新初始化）
  const [revision, setRevision] = useState(0)
  const [disabling, setDisabling] = useState(false)

  const load = useCallback(async (fresh = false) => {
    setLoading(true)
    setError('')
    try {
      const data = fresh
        ? await fetchMcpToolDetailFresh(toolId)
        : await getMcpToolDetail(toolId)
      setDetail(data)
    } catch (e) {
      setError(errMessage(e))
    } finally {
      setLoading(false)
    }
  }, [toolId])

  useEffect(() => {
    setTab('overview')
    void load()
  }, [load])

  const handleMutated = async (toolIds: number[]) => {
    invalidateMcpToolDetailCache(toolIds)
    await onMutated()
    await load(true)
    setRevision((r) => r + 1)
  }

  const disableTool = async (tool: McpTool) => {
    setDisabling(true)
    try {
      await disableMcpTool(tool.id)
      toast.success('工具已停用；写调用立即被拦截，读调用最迟 15 秒退出候选')
      await handleMutated([tool.id])
    } catch (e) {
      toast.error(`停用失败: ${errMessage(e)}`)
    } finally {
      setDisabling(false)
    }
  }

  return (
    <DetailDrawer
      open
      width="lg"
      onClose={onClose}
      title={detail?.capability ?? `工具 #${toolId}`}
      subtitle={detail && (
        <span className="flex flex-wrap items-center gap-1.5">
          <DynamicSourceBadge tool={detail} />
          <span className="font-mono">{detail.tool_name}</span>
          <span className="font-mono text-[11px]">
            hash {detail.content_hash.slice(0, 8)} · v{detail.last_seen_version}
          </span>
        </span>
      )}
      tabs={TOOL_DRAWER_TABS}
      activeTab={tab}
      onTabChange={setTab}
      actions={detail?.status === 'approved' ? (
        <AssetActionButton disabled={disabling} onClick={() => void disableTool(detail)}>
          {disabling ? <Loader2 size={13} className="animate-spin" /> : null}
          停用工具
        </AssetActionButton>
      ) : undefined}
    >
      {loading && !detail ? (
        <Skeleton rows={6} cols={2} />
      ) : error && !detail ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : detail ? (
        <div key={revision} className="space-y-4 text-[12.5px]">
          {tab === 'overview' && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-text-muted">状态：</span>
                <StatusChip tone={MCP_STATUS_META[detail.status]?.tone ?? 'neutral'}>
                  {MCP_STATUS_META[detail.status]?.label ?? detail.status}
                </StatusChip>
                {detail.schema_changed && (
                  <StatusChip tone="danger">契约漂移（需重新审核）</StatusChip>
                )}
                {detail.retired_reason && (
                  <StatusChip tone="neutral">上游已消失</StatusChip>
                )}
              </div>
              <div className="grid gap-x-4 gap-y-2 md:grid-cols-2">
                <div className="flex items-center gap-2">
                  <span className="w-14 shrink-0 text-text-muted">连接</span>
                  <span>{detail.connection_name ?? detail.connection_id}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-14 shrink-0 text-text-muted">上游工具</span>
                  <span className="font-mono text-[11px]">{detail.tool_name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-14 shrink-0 text-text-muted">业务域</span>
                  <span>{domainLabel(
                    typeof detail.governance?.domain === 'string' ? detail.governance.domain : '')}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-14 shrink-0 text-text-muted">授权产品</span>
                  <span>
                    {(detail.grants ?? []).length > 0
                      ? (detail.grants ?? []).map((g) => g.product_key).join('、')
                      : '未授权'}
                  </span>
                </div>
              </div>
              <div>
                <div className="mb-1 text-text-muted">描述</div>
                <div className="rounded-lg border border-border-subtle bg-surface-base px-3 py-2 leading-relaxed">
                  {detail.description || <span className="text-text-muted">（无描述）</span>}
                </div>
              </div>
              {detail.reviewed_at && (
                <div className="text-[11px] text-text-muted">
                  最近审核 {new Date(detail.reviewed_at).toLocaleString()}（by {detail.reviewed_by || '—'}）
                </div>
              )}
            </div>
          )}

          {tab === 'schema' && (
            <div className="space-y-3">
              {detail.schema_changed && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700">
                  上游 Schema 已变化：当前 hash {detail.content_hash.slice(0, 8)} ≠
                  已审核 hash {detail.approved_hash.slice(0, 8)}，需重新审核后才会回到运行时。
                </div>
              )}
              <div>
                <div className="mb-1 text-text-muted">输入参数（发现快照）</div>
                {invokeSchemaProperties(detail).length === 0 ? (
                  <div className="text-text-muted">无参数</div>
                ) : (
                  <table className="w-full">
                    <thead>
                      <tr className="text-left text-[11px] text-text-muted">
                        <th className="py-1 pr-3 font-normal">参数</th>
                        <th className="py-1 pr-3 font-normal">类型</th>
                        <th className="py-1 font-normal">说明（上游声明，不可信）</th>
                      </tr>
                    </thead>
                    <tbody>
                      {invokeSchemaProperties(detail).map(([pname, pdef]) => (
                        <tr key={pname} className="border-t border-border-subtle">
                          <td className="py-1 pr-3 font-mono">{pname}</td>
                          <td className="py-1 pr-3">{pdef?.type ?? '-'}</td>
                          <td className="py-1 text-text-muted">{pdef?.description ?? ''}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              <div>
                <div className="mb-1 text-text-muted">快照版本（漂移对比：发现版本 / 内容指纹）</div>
                {(detail.snapshots?.length ?? 0) === 0 ? (
                  <div className="text-text-muted">暂无快照记录</div>
                ) : (
                  <ul className="space-y-1.5">
                    {(detail.snapshots ?? []).slice(0, 5).map((snap, idx) => (
                      <li key={`${snap.discovery_version}-${idx}`}
                        className={`rounded border px-2.5 py-1.5 font-mono text-[11px] ${
                          idx === 0 ? 'border-accent/50 bg-accent/5' : 'border-border-subtle'
                        }`}>
                        v{snap.discovery_version} · hash {snap.content_hash.slice(0, 8)}
                        <span className="ml-2 font-sans text-text-muted">
                          {new Date(snap.discovered_at).toLocaleString()}
                        </span>
                        {idx === 0 && (
                          <span className="ml-2 rounded bg-accent/10 px-1 font-sans text-[10px] text-accent">当前</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}

          {tab === 'policy' && (
            <ReviewPolicyForm tool={detail} onDone={() => handleMutated([detail.id])} />
          )}

          {tab === 'ai' && (
            <SemanticContractSection tool={detail} onDone={() => handleMutated([detail.id])} />
          )}

          {tab === 'debug' && <DebugInvokeForm tool={detail} />}

          {tab === 'history' && <ToolHistorySection tool={detail} />}
        </div>
      ) : null}
    </DetailDrawer>
  )
}
