import { describe, expect, it } from 'vitest'
import { deriveRunKind } from './runKind'

describe('deriveRunKind（workflow_kind DTO 字段优先）', () => {
  it('rag_query 直接判为 RAG 链，不看 workflow_name', () => {
    expect(deriveRunKind({ workflow_kind: 'rag_query', workflow_name: 'agent' }))
      .toEqual({ key: 'rag', label: 'RAG 链' })
  })

  it('knowledge_index 直接判为索引流水线', () => {
    expect(deriveRunKind({ workflow_kind: 'knowledge_index', workflow_name: '' }))
      .toEqual({ key: 'index', label: '索引流水线' })
  })

  it('langgraph_workflow 保留名称映射细分（agent → Agent 图）', () => {
    expect(deriveRunKind({ workflow_kind: 'langgraph_workflow', workflow_name: 'agent' }))
      .toEqual({ key: 'agent', label: 'Agent 图' })
  })

  it('langgraph_workflow 内定时任务仍按名称映射', () => {
    expect(deriveRunKind({ workflow_kind: 'langgraph_workflow', workflow_name: 'daily_report' }))
      .toEqual({ key: 'wf', label: '定时任务' })
  })

  it('存量数据无 workflow_kind 时名称映射兜底', () => {
    expect(deriveRunKind({ workflow_kind: '', workflow_name: 'rag_agent' }))
      .toEqual({ key: 'rag', label: 'RAG 链' })
    expect(deriveRunKind({ workflow_kind: 'other', workflow_name: 'admin-probe-x' }))
      .toEqual({ key: 'probe', label: '健康探测' })
  })

  it('未知名称兜底为工作流/未记录', () => {
    expect(deriveRunKind({ workflow_kind: '', workflow_name: 'custom_flow' }))
      .toEqual({ key: 'wf', label: '工作流' })
    expect(deriveRunKind({ workflow_kind: '', workflow_name: '' }))
      .toEqual({ key: 'wf', label: '未记录' })
  })
})
