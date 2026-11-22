'use client'

/**
 * 文件评测集目录（批次 1.1 评测集查看）：展示仓库治理的文件套件
 * （backend/evaluation/datasets/<域>/，manifest 治理），支持查看题目样例
 * （脱敏只读）。与 V2 数据集方案（数据库）并列，是"各智能体测什么题"
 * 的权威视图。数据获取走手动 fetch（与 DatasetsTab 组件树风格一致）。
 */

import { useCallback, useEffect, useState } from 'react'
import { evaluationService, type DatasetCatalogItem, type DatasetCaseSamples } from '@/api/evaluation'
import { errMessage } from '@/api/client'
import { Badge, Card, ctl, Drawer, Empty, TableWrap, Td, Th, timeShort } from './evalUi'
import { domainLabel, moduleToDomain } from '../domains'

const SAMPLE_LIMIT = 8

function fmtCoverage(coverage: DatasetCatalogItem['coverage']): string {
  const parts: string[] = []
  const tiers = coverage?.tiers ? Object.entries(coverage.tiers) : []
  if (tiers.length) parts.push(`分层 ${tiers.map(([k, v]) => `${k}×${v}`).join(' / ')}`)
  if (coverage?.verified_count) parts.push(`已核验 ${coverage.verified_count}`)
  return parts.join(' · ') || '—'
}

export default function FileSuitesPanel() {
  const [sampleDataset, setSampleDataset] = useState('')
  const [datasets, setDatasets] = useState<DatasetCatalogItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadCatalog = useCallback(async () => {
    setLoading(true); setError('')
    try { setDatasets(await evaluationService.listDatasets()) }
    catch (cause) { setError(errMessage(cause)) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void loadCatalog() }, [loadCatalog])

  return <Card
    title="评测集（文件套件）"
    subtitle={'仓库治理的评测题集：各智能体测什么题、多少题、最近一次结果；点「样例」看题目内容（脱敏只读）。'}
  >
    {loading ? <Empty text="正在读取评测集目录…" />
      : error ? <div className="space-y-2 py-5 text-center">
        <p role="alert" className="text-xs text-red-700">{error}</p>
        <button type="button" className={ctl} onClick={() => void loadCatalog()}>重新读取</button>
      </div>
      : datasets.length === 0 ? <Empty text="仓库暂无文件评测集（datasets/ 目录为空）。" />
      : <TableWrap minWidth={960}>
        <thead className="bg-bg-elevated"><tr><Th>域</Th><Th>评测集</Th><Th>版本</Th><Th>题数</Th><Th>覆盖 / 分层</Th><Th>知识库</Th><Th>最近运行</Th><Th end>操作</Th></tr></thead>
        <tbody className="divide-y divide-border-subtle">
          {datasets.map(item => {
            const latest = (item.latest_run ?? null) as { run_id?: string; pass_rate?: number; timestamp?: string } | null
            return <tr key={item.dataset_id}>
              <Td><Badge text={domainLabel(moduleToDomain(item.module))} tone="blue" /></Td>
              <Td><span className="block text-text-primary">{item.dataset_id}</span><span className="text-[11px] text-text-muted">{item.kind === 'probe' ? '探针集' : '核心集'} · {item.review_status}</span></Td>
              <Td>{item.dataset_version}</Td>
              <Td>{item.case_count}</Td>
              <Td className="max-w-56 break-words">{fmtCoverage(item.coverage)}</Td>
              <Td>{item.kb_id || '—'}</Td>
              <Td>{latest ? <span className="text-xs">{typeof latest.pass_rate === 'number' ? `${(latest.pass_rate * 100).toFixed(1)}%` : '—'} · {timeShort(latest.timestamp ?? null)}</span> : <span className="text-text-muted">—</span>}</Td>
              <Td end><button type="button" className={ctl} onClick={() => setSampleDataset(item.dataset_id)}>样例</button></Td>
            </tr>
          })}
        </tbody>
      </TableWrap>}
    <SampleDrawer datasetId={sampleDataset} onClose={() => setSampleDataset('')} />
  </Card>
}

function SampleDrawer({ datasetId, onClose }: { datasetId: string; onClose: () => void }) {
  const [samples, setSamples] = useState<DatasetCaseSamples | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!datasetId) { setSamples(null); return }
    let cancelled = false
    setLoading(true); setError('')
    evaluationService.getDatasetSamples(datasetId, SAMPLE_LIMIT)
      .then(data => { if (!cancelled) setSamples(data) })
      .catch((cause: unknown) => { if (!cancelled) setError(errMessage(cause)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [datasetId])

  const items = samples?.samples ?? []
  return <Drawer
    open={Boolean(datasetId)}
    title={`评测集样例 · ${datasetId}`}
    subtitle={samples ? `共 ${samples.total} 题，展示前 ${items.length} 题（脱敏只读）` : '正在读取题目样例…'}
    onClose={onClose}
  >
    {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
    {loading && <Empty text="正在读取题目样例…" />}
    {samples && items.length === 0 && <Empty text="该评测集暂无题目。" />}
    {items.map(sample => <div key={sample.id} className="mb-3 rounded-lg border border-border-subtle p-3">
      <p className="text-xs font-medium text-text-primary">
        {sample.id}{sample.tier && <span className="ml-1.5"><Badge text={sample.tier} tone="gray" /></span>}
      </p>
      {sample.input_summary && <p className="mt-1.5 text-xs text-text-secondary"><span className="text-text-muted">输入：</span>{sample.input_summary}</p>}
      {sample.expected_summary && <p className="mt-1 text-xs text-text-secondary"><span className="text-text-muted">期望：</span>{sample.expected_summary}</p>}
    </div>)}
  </Drawer>
}
