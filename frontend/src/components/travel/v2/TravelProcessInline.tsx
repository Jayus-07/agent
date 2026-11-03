'use client'

/**
 * TravelProcessInline — 原型 tp-proc：助手气泡内嵌的过程行（对齐原型 C1）。
 *
 * 运行中 = spinner + 当前阶段单行替换式；结束 = 折叠为「✓ 已完成 N 个步骤」，
 * 可展开阶段清单与 Tool 耗时。数据只读自 reduceTravelStreamEvent 归并结果，
 * 不新增事件消费；阶段名按后端图节点名（backend/travel/graph_state.py）映射
 * 为原型同款中文标签，未登记的节点名原样展示（不伪造进度）。
 */
import { useState } from 'react'
import type { TravelProcessState } from '@/components/travel/travelRuntime'

const STAGE_LABELS: Record<string, string> = {
  travel_slot_filler: '槽位补全',
  travel_poi_expert: 'POI 候选检索',
  travel_transit_expert: '路线估算',
  travel_weather_expert: '天气核查',
  travel_lodging_expert: '住宿匹配',
  travel_budget_expert: '预算核算',
  travel_risk_expert: '风险评估',
  travel_validator: '排程校验',
  travel_repair: '行程修复',
  travel_partial_replan: '局部重排',
  travel_auxiliary_tasks: '辅助查询',
  travel_reporter: '行程单渲染',
  travel_supervisor: '结果汇总',
}

export function travelStageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage
}

export default function TravelProcessInline({ process }: { process: TravelProcessState }) {
  const [open, setOpen] = useState(false)
  const stageEntries = Object.entries(process.stages)
  const runningStage = [...stageEntries].reverse().find(([, stage]) => stage.status === 'running')?.[0]

  if (process.status === 'running') {
    return (
      <div className="flex items-center gap-2 text-[11px] text-[#526b86]">
        <span className="h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-[#b9d3fa] border-t-[#2878f5]" aria-hidden />
        <span>{runningStage ? `正在${travelStageLabel(runningStage)}…` : '正在连接旅游 Agent…'}</span>
      </div>
    )
  }

  // 无阶段也无 Tool 的轮次（如纯澄清快答）没有可折叠的过程，渲染为空
  if (stageEntries.length === 0 && process.tools.length === 0) return null

  const failedCount = stageEntries.filter(([, stage]) => stage.status === 'failed').length
  return (
    <div>
      <button type="button" aria-expanded={open} onClick={() => setOpen((current) => !current)} className="flex items-center gap-1.5 text-[11px] font-semibold text-[#246bd2]">
        {failedCount > 0 ? `⚠ 已执行 ${stageEntries.length} 个步骤 · ${failedCount} 项失败` : `✓ 已完成 ${stageEntries.length} 个步骤`}
        <span aria-hidden className={`inline-block transition-transform ${open ? 'rotate-90' : ''}`}>▸</span>
      </button>
      {open && (
        <div className="mt-1.5 flex flex-col gap-0.5 border-t border-dashed border-[#dbe7f4] pt-1.5">
          {stageEntries.map(([stage, stageState]) => (
            <span key={stage} className="text-[11px] leading-5 text-[#5d7490]">{stageState.status === 'failed' ? '✗' : '✓'} {travelStageLabel(stage)}</span>
          ))}
          {process.tools.map((tool, index) => (
            <span key={`${tool.toolCallId ?? ''}-${tool.tool}-${index}`} className="text-[11px] leading-5 text-[#5d7490]">
              · {tool.tool}{typeof tool.durationMs === 'number' ? ` · ${tool.durationMs}ms` : ''}{tool.status !== 'success' ? ` · ${tool.status}` : ''}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
