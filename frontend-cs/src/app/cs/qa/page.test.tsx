// cs-workstation-v2 P6：质检中心页面交互（任务表/选中加载/AI 预填/拖分合计/
// 提交改状态/申诉发起/supervisor 终审维持·改判/角色 Tab 可见性）。
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

// 角色可切换（agent/supervisor 两组用例共用一个模块 mock）
const me = vi.hoisted(() => ({ agentId: 'p6t-agent-a', role: 'agent' }))

const api = vi.hoisted(() => ({
  getMyStatus: vi.fn(async () => ({
    agent_id: me.agentId,
    status: 'online',
    accepting: true,
    presence_online: true,
    cs_role: me.role,
  })),
  listQaTasks: vi.fn(async (params?: { status?: string; appeal?: string }) => {
    if (params?.appeal === 'appealing') {
      return {
        items: [
          {
            id: 9,
            conversation_id: 'conv-p6c-3',
            agent_id: 'p6t-agent-a',
            batch_date: '2026-10-31',
            source: 'human',
            status: 'scored',
            scores: { service: 15, accuracy: 20, compliance: 18, timeliness: 18 },
            total_score: 71,
            verdict: 'fail',
            note: '两次响应超时且未安抚',
            ai_prefill: null,
            scored_by: 'p6t-sup',
            scored_at: '2026-10-31T12:00:00Z',
            appeal_status: 'appealing',
            appeal_reason: '已提前告知物流延迟，处理仍在时效内',
            appeal_decision: null,
            appeal_opinion: null,
            final_score: null,
            final_verdict: null,
            created_at: '2026-10-31T08:00:00Z',
          },
        ],
        total: 1,
        scope: 'all',
      }
    }
    return { items: baseTasks(), total: 2, scope: 'self' }
  }),
  submitQaScore: vi.fn(
    async (_id: number, body: { verdict: string; draft?: boolean }) => ({
      task: {
        id: 1,
        total_score: 80,
        verdict: body.verdict,
        status: body.draft === true ? 'draft' : 'scored',
      },
    }),
  ),
  requestQaAiPrefill: vi.fn(async () => ({
    task: {
      id: 1,
      ai_prefill: {
        scores: { service: 20, accuracy: 22, compliance: 21, timeliness: 17 },
        total: 80,
        verdict: 'pass',
        note: '（AI 预填建议 · source=ai，请人工终审）主要扣分：处理时效偏长',
      },
    },
  })),
  appealQaScore: vi.fn(async () => ({ task: { id: 2, appeal_status: 'appealing' } })),
  decideQaAppeal: vi.fn(async (_id: number, body: { decision: string }) => ({
    task: { id: 9, appeal_status: body.decision },
  })),
  getQaReport: vi.fn(async () => ({
    items: [
      {
        batch_date: '2026-10-31',
        sampled: 3,
        scored: 2,
        pass_count: 1,
        pass_rate: 0.5,
        avg_score: 81.5,
        appeals: 1,
        top_deduction: '处理时效（2）',
      },
    ],
  })),
}))

function baseTasks() {
  return [
    {
      id: 1,
      conversation_id: 'conv-p6a-1',
      agent_id: 'p6t-agent-a',
      batch_date: '2026-10-31',
      source: 'ai',
      status: 'pending',
      scores: { service: null, accuracy: null, compliance: null, timeliness: null },
      total_score: null,
      verdict: null,
      note: null,
      ai_prefill: null,
      scored_by: null,
      scored_at: null,
      appeal_status: 'none',
      appeal_reason: null,
      appeal_decision: null,
      appeal_opinion: null,
      final_score: null,
      final_verdict: null,
      created_at: '2026-10-31T07:00:00Z',
    },
    {
      id: 2,
      conversation_id: 'conv-p6b-2',
      agent_id: 'p6t-agent-a',
      batch_date: '2026-10-30',
      source: 'human',
      status: 'scored',
      scores: { service: 24, accuracy: 23, compliance: 24, timeliness: 21 },
      total_score: 92,
      verdict: 'pass',
      note: '流程完整，安抚到位',
      ai_prefill: null,
      scored_by: 'p6t-sup',
      scored_at: '2026-10-30T12:00:00Z',
      appeal_status: 'none',
      appeal_reason: null,
      appeal_decision: null,
      appeal_opinion: null,
      final_score: null,
      final_verdict: null,
      created_at: '2026-10-30T07:00:00Z',
    },
  ]
}

vi.mock('@/cs2/workspace/api', () => api)

import Cs2QaPage from '@/app/cs/qa/page'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

let mounted: { container: HTMLElement; root: Root } | null = null

afterEach(() => {
  if (mounted) {
    act(() => mounted?.root.unmount())
    mounted.container.remove()
    mounted = null
  }
  me.agentId = 'p6t-agent-a'
  me.role = 'agent'
  vi.clearAllMocks()
})

async function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => {
    root.render(<Cs2QaPage />)
  })
  mounted = { container, root }
  return container
}

function setInputValue(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto =
    el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set
  setter?.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

function findButton(c: HTMLElement, text: string): HTMLButtonElement | undefined {
  return Array.from(c.querySelectorAll('button')).find((b) =>
    b.textContent?.includes(text),
  )
}

describe('Cs2QaPage（P6 质检中心）', () => {
  it('agent 视角渲染任务表两行；申诉处理/质检日报 Tab 隐藏', async () => {
    const c = await mount()
    await act(async () => {})
    expect(api.getMyStatus).toHaveBeenCalled()
    expect(api.listQaTasks).toHaveBeenCalledTimes(1)
    expect(c.textContent).toContain('#conv-p6a')
    expect(c.textContent).toContain('待评分')
    expect(c.textContent).toContain('92')
    expect(c.textContent).toContain('合格')
    // 角色可见性：agent 无申诉处理/质检日报
    expect(findButton(c, '申诉处理')).toBeUndefined()
    expect(findButton(c, '质检日报')).toBeUndefined()
    expect(findButton(c, '抽检任务')).toBeDefined()
  })

  it('选中待评分任务加载表单；AI 预填填入建议并提示人工终审；拖分实时合计', async () => {
    const c = await mount()
    await act(async () => {})
    // 选中待评分任务（onClick 绑定在单元格上）
    await act(async () => {
      ;(c.querySelector('tbody tr td') as HTMLElement | null)?.click()
    })
    expect(c.textContent).toContain('评分：会话 #conv-p6a')
    expect(c.textContent).toContain('AI 预填建议')
    // 初始合计 0
    expect(c.textContent).toContain('0')

    // AI 预填 → 建议分填入 + 实时合计 80
    await act(async () => {
      findButton(c, 'AI 预填建议')?.click()
    })
    expect(api.requestQaAiPrefill).toHaveBeenCalledWith(1)
    expect(c.textContent).toContain('80')
    expect(c.textContent).toContain('请人工终审')

    // 拖动第一维 20→25 → 合计 85
    const slider = c.querySelector('input[type="range"]') as HTMLInputElement
    await act(async () => {
      setInputValue(slider, '25')
    })
    expect(c.textContent).toContain('85')
  })

  it('提交评分改状态：调用 score 接口并刷新列表；已评分任务表单锁定', async () => {
    const c = await mount()
    await act(async () => {})
    await act(async () => {
      ;(c.querySelector('tbody tr td') as HTMLElement | null)?.click()
    })
    await act(async () => {
      findButton(c, 'AI 预填建议')?.click()
    })
    await act(async () => {
      findButton(c, '提交评分')?.click()
    })
    expect(api.submitQaScore).toHaveBeenCalledWith(
      1,
      expect.objectContaining({
        service: 20,
        accuracy: 22,
        compliance: 21,
        timeliness: 17,
        verdict: 'pass',
        draft: false,
      }),
    )
    expect(c.textContent).toContain('评分已提交：80 分 · 合格')

    // 选中已评分任务 → 表单锁定（滑杆 disabled + 已提交标识）
    const rows = Array.from(c.querySelectorAll('tbody tr'))
    await act(async () => {
      rows[1]?.querySelector('td')?.click()
    })
    expect(c.textContent).toContain('（已提交）')
    const sliders = Array.from(
      c.querySelectorAll('input[type="range"]'),
    ) as HTMLInputElement[]
    expect(sliders.every((s) => s.disabled)).toBe(true)
    expect(findButton(c, '提交评分')).toBeUndefined()
  })

  it('被检坐席本人可对已评分任务发起申诉', async () => {
    const c = await mount()
    await act(async () => {})
    const rows = Array.from(c.querySelectorAll('tbody tr'))
    await act(async () => {
      rows[1]?.querySelector('td')?.click()
    })
    const reason = c.querySelector(
      'textarea[aria-label="申诉理由"]',
    ) as HTMLTextAreaElement
    expect(reason).toBeDefined()
    await act(async () => {
      setInputValue(reason, '已提前告知物流延迟，处理仍在时效内')
    })
    await act(async () => {
      findButton(c, '提交申诉')?.click()
    })
    expect(api.appealQaScore).toHaveBeenCalledWith(
      2,
      '已提前告知物流延迟，处理仍在时效内',
    )
    expect(c.textContent).toContain('申诉已提交')
  })

  it('supervisor 视角可见申诉处理/质检日报；终审改判提交终审分与意见', async () => {
    me.role = 'supervisor'
    const c = await mount()
    await act(async () => {})
    expect(findButton(c, '申诉处理')).toBeDefined()
    expect(findButton(c, '质检日报')).toBeDefined()

    // 申诉处理 Tab：加载 appealing 列表
    await act(async () => {
      findButton(c, '申诉处理')?.click()
    })
    expect(api.listQaTasks).toHaveBeenCalledWith({ appeal: 'appealing' })
    expect(c.textContent).toContain('已提前告知物流延迟，处理仍在时效内')
    expect(c.textContent).toContain('申诉中')

    // 终审 → 改判（终审分 + 结论 + 意见必填）
    await act(async () => {
      findButton(c, '终审')?.click()
    })
    const scoreInput = c.querySelector(
      'input[aria-label="终审得分"]',
    ) as HTMLInputElement
    const opinionInput = c.querySelector(
      'input[aria-label="终审意见"]',
    ) as HTMLInputElement
    const verdictSelect = c.querySelector(
      'select[aria-label="终审结论"]',
    ) as HTMLSelectElement
    // 默认继承原评分（fail），显式改判为合格
    await act(async () => {
      setInputValue(scoreInput, '85')
      setInputValue(opinionInput, '回复仍在时效内，改判合格')
      const setter = Object.getOwnPropertyDescriptor(
        HTMLSelectElement.prototype,
        'value',
      )?.set
      setter?.call(verdictSelect, 'pass')
      verdictSelect.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await act(async () => {
      findButton(c, '改判')?.click()
    })
    expect(api.decideQaAppeal).toHaveBeenCalledWith(9, {
      decision: 'overturned',
      opinion: '回复仍在时效内，改判合格',
      final_score: 85,
      final_verdict: 'pass',
    })
    expect(c.textContent).toContain('已改判，终审分生效')
  })

  it('supervisor 质检日报 Tab 渲染抽检聚合（含人工抽检数据）', async () => {
    me.role = 'supervisor'
    const c = await mount()
    await act(async () => {})
    await act(async () => {
      findButton(c, '质检日报')?.click()
    })
    expect(api.getQaReport).toHaveBeenCalledWith(7)
    expect(c.textContent).toContain('10-31')
    expect(c.textContent).toContain('50.0%')
    expect(c.textContent).toContain('处理时效（2）')
  })
})
