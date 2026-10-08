/**
 * planState 回归测试 — 旅游页两条规划通道共用的合并规则
 *
 * 项目惯例：无 testing-library，纯函数直测（同 navConfig.test.ts）。
 * 这里锁的是**已经踩过的坑**：原 page.tsx 无论后端回什么都是
 * `setPlan(data)` —— 后端回一句追问（itinerary=null）就把用户刚拿到的
 * 行程整块清掉，页面只剩一句追问，用户以为行程没了。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  EMPTY_PLAN_STATE,
  adoptConversationId,
  applyPlanResponse,
  buildTravelBriefInput,
  composePlanMessage,
  describePlanReply,
  formatDayDate,
  itineraryTotal,
  readConversationId,
  readPlanState,
  persistPlanState,
  previewPlanResponse,
  clearPendingPlan,
  reconcilePlanWithLatest,
  sanitizeTravelReply,
  rotateConversationId,
  type PlanFormInput,
  type PlanState,
} from './planState'
import type { Itinerary, PlanResponse } from '@/api/travel'

function makeItinerary(overrides: Partial<Itinerary> = {}): Itinerary {
  return {
    brief: {
      destination: '杭州', origin: '', days: 2, party_size: 2, start_date: null,
      budget_cny: null, preferences: ['自然'], must_go: [], avoid: [],
      pace: 'moderate', diet: '', lodging: '', transport: '',
    },
    days: [],
    cost: { tickets: 100, meals: 200, lodging: 300, transit: 40 },
    status: 'ready',
    plan_version: 1,
    warnings: [],
    ...overrides,
  }
}

const emptyForm: PlanFormInput = {
  destination: '', days: '', startDate: '', partySize: '',
  budget: '', pace: '', preferences: [], extra: '',
}

describe('composePlanMessage — 表单转域图消息', () => {
  it('目的地留空时用推荐话术兜底（不能发空消息，后端 min_length=1）', () => {
    expect(composePlanMessage(emptyForm)).toBe('帮我推荐个地方规划行程')
  })

  it('按固定顺序拼接，并带上前缀让后端好抽槽', () => {
    const message = composePlanMessage({
      destination: ' 杭州 ', days: '3', startDate: '2026-10-01', partySize: '2',
      budget: '3000', pace: 'relaxed', preferences: ['自然', '美食'], extra: '必去西湖',
    })
    expect(message).toBe('杭州，3天，2026-10-01出发，2个人，预算3000元，节奏轻松点，喜欢自然、美食，必去西湖')
  })

  it('空字段整段不出现（不留空串让域图误判用户表达了空值）', () => {
    expect(composePlanMessage({ ...emptyForm, destination: '福州', days: '2' }))
      .toBe('福州，2天')
  })

  it('出发地存在时放在目的地后，供域图优先抽取', () => {
    expect(composePlanMessage({ ...emptyForm, destination: '厦门', origin: '福州', days: '2' }))
      .toBe('厦门，从福州出发，2天')
  })

  it('表单同时投影为结构化 Brief，不把数值字段留作自由文本', () => {
    expect(buildTravelBriefInput({
      destination: ' 厦门 ', origin: ' 福州 ', days: '2',
      startDate: '2026-10-10', partySize: '2', budget: '2000',
      pace: 'relaxed', preferences: ['美食'], extra: '顺便查高铁',
    })).toEqual({
      destination: '厦门', origin: '福州', days: 2,
      start_date: '2026-10-10', party_size: 2, budget_cny: 2000,
      pace: 'relaxed', preferences: ['美食'],
    })
  })
})

describe('applyPlanResponse — 追问/失败不得清掉已展示的行程', () => {
  const withPlan: PlanState = { plan: { status: 'ready', final_answer: 'ok', itinerary: makeItinerary() } as PlanResponse, pending: null, notice: '', discarded: [] }

  it('回复带行程 → 整体替换', () => {
    const next = applyPlanResponse(withPlan, {
      status: 'ready', final_answer: '第二版', itinerary: makeItinerary({ plan_version: 2 }),
    })
    expect(next.plan?.itinerary?.plan_version).toBe(2)
    expect(next.pending).toBeNull()
    expect(next.notice).toBe('')
  })

  it('草案预览只更新 pending，不替换当前行程', () => {
    const next = previewPlanResponse(withPlan, {
      status: 'ready', final_answer: '第三版预览',
      itinerary: makeItinerary({
        plan_version: 3,
        brief: { ...makeItinerary().brief, days: 5 },
      }),
      plan_status: 'waiting_confirmation',
    })
    expect(next.plan?.itinerary?.plan_version).toBe(1)
    expect(next.pending?.itinerary?.plan_version).toBe(3)
  })

  it('应用或放弃草案后只保留一个明确状态', () => {
    const preview = previewPlanResponse(withPlan, {
      status: 'ready', final_answer: '预览', itinerary: makeItinerary({ plan_version: 2 }),
    })
    expect(clearPendingPlan(preview).pending).toBeNull()
    expect(applyPlanResponse(preview, {
      status: 'ready', final_answer: '已应用', itinerary: makeItinerary({ plan_version: 2 }),
    }).pending).toBeNull()
  })

  it('回复不带行程（追问）→ 行程留在原地，只把追问落到 notice', () => {
    const next = applyPlanResponse(withPlan, {
      status: 'needs_clarification', final_answer: '打算玩几天？', itinerary: null,
    })
    expect(next.plan).toBe(withPlan.plan)
    expect(next.notice).toBe('打算玩几天？')
  })

  it('回复不带行程且话术为空 → notice 有兜底文案，不出现空白提示', () => {
    const next = applyPlanResponse(EMPTY_PLAN_STATE, {
      status: 'failed', final_answer: '', itinerary: null,
    })
    expect(next.plan).toBeNull()
    expect(next.notice).toBe('这次没能生成行程，换个说法再试一次')
  })
})

describe('describePlanReply — 抽屉里「到底改没改」的标签', () => {
  it('出单 → ok 标签带版本号', () => {
    expect(describePlanReply({ status: 'ready', final_answer: '', itinerary: makeItinerary({ plan_version: 3 }) }))
      .toEqual({ tag: '行程已更新 · v3', tone: 'ok' })
  })

  it('追问 / 失败 → warn 标签区分原因', () => {
    expect(describePlanReply({ status: 'needs_clarification', final_answer: '几个人？', itinerary: null }).tone).toBe('warn')
    expect(describePlanReply({ status: 'failed', final_answer: '服务不可用', itinerary: null }).tag).toBe('规划失败')
  })
})

describe('会话线程 — 刷新后仍接着改同一份行程', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
  })

  it('rotate 后落盘，read 能取回同一个 id（原实现每次挂载都新生成）', () => {
    const id = rotateConversationId()
    expect(id).toBeTruthy()
    expect(readConversationId()).toBe(id)
  })

  it('adopt 收养历史线程：切到指定 id 且落盘（恢复历史规划不产生新线程）', () => {
    rotateConversationId() // 先有一个「当前」线程
    const restored = adoptConversationId('conv-restored')
    expect(restored).toBe('conv-restored')
    expect(readConversationId()).toBe('conv-restored')
  })

  it('storage 不可用（隐私模式）时退化为新 id，不抛异常', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(() => rotateConversationId()).not.toThrow()
    spy.mockRestore()
  })

  it('刷新后恢复同一会话的当前行程与待应用草案', () => {
    const state = {
      plan: { status: 'ready', final_answer: 'ok', itinerary: makeItinerary({ plan_version: 2 }) },
      pending: { status: 'ready', final_answer: 'preview', itinerary: makeItinerary({ plan_version: 3 }) },
      notice: '',
      discarded: [],
    } as PlanState
    persistPlanState(state)
    expect(readPlanState()).toEqual(state)
  })

  it('服务端已确认的新版本覆盖旧 sessionStorage，刷新后页面与 DB 对齐', () => {
    const stale: PlanState = {
      plan: { status: 'ready', final_answer: '旧内容', itinerary: makeItinerary({ plan_version: 2 }) },
      pending: null,
      notice: '',
      discarded: [],
    }
    const next = reconcilePlanWithLatest(stale, {
      conversation_id: 'conv-1',
      plan_version: 4,
      plan_status: 'confirmed',
      destination: '杭州',
      created_at: '',
      itinerary: makeItinerary({ plan_version: 4 }),
    })
    expect(next.plan?.itinerary?.plan_version).toBe(4)
    expect(next.pending).toBeNull()
  })

  it('服务端待确认的新版本进入 pending，不覆盖当前 active', () => {
    const stale: PlanState = {
      plan: { status: 'ready', final_answer: '旧内容', itinerary: makeItinerary({ plan_version: 2 }) },
      pending: null,
      notice: '',
      discarded: [],
    }
    const next = reconcilePlanWithLatest(stale, {
      conversation_id: 'conv-1',
      plan_version: 3,
      plan_status: 'waiting_confirmation',
      destination: '杭州',
      created_at: '',
      itinerary: makeItinerary({ plan_version: 3 }),
    })
    expect(next.plan?.itinerary?.plan_version).toBe(2)
    expect(next.pending?.itinerary?.plan_version).toBe(3)
  })

  it('本地缓存全空时，服务端同时恢复 active 与 draft', () => {
    const next = reconcilePlanWithLatest(EMPTY_PLAN_STATE, {
      conversation_id: 'conv-1',
      plan_version: 5,
      plan_status: 'waiting_confirmation',
      destination: '杭州',
      created_at: '',
      itinerary: makeItinerary({ plan_version: 5 }),
      active_plan_version: 4,
      active_plan_status: 'confirmed',
      active_itinerary: makeItinerary({ plan_version: 4 }),
    })
    expect(next.plan?.itinerary?.plan_version).toBe(4)
    expect(next.pending?.itinerary?.plan_version).toBe(5)
  })
})

describe('展示小工具', () => {
  it('无日期给「未定日期」，有日期给中文周几', () => {
    expect(formatDayDate(null)).toBe('未定日期')
    expect(formatDayDate('2026-10-01')).toBe('2026-10-01 周四')
  })

  it('合计：cost.total 缺失时（后端 @property 不进 model_dump）前端自求和', () => {
    expect(itineraryTotal(undefined)).toBe(0)
    expect(itineraryTotal({ tickets: 1, meals: 2, lodging: 3, transit: 4 })).toBe(10)
    expect(itineraryTotal({ tickets: 1, meals: 2, lodging: 3, transit: 4, total: 99 })).toBe(99)
  })

  it('助手回复不透传旧实例的 seed、费用预估和内部置信度', () => {
    const sanitized = sanitizeTravelReply([
      '# 厦门 3 天行程',
      '',
      '行程费用按常见消费水平估算，详见下方「费用预估」',
      '',
      '## 数据来源',
      '',
      'seed:local — 本地示例数据（未经实时校验）',
      '',
      '*行程 v1 —— 置信度 0.80（高）*',
    ].join('\n'))
    expect(sanitized).not.toContain('seed:local')
    expect(sanitized).not.toContain('费用预估')
    expect(sanitized).not.toContain('置信度')
    expect(sanitized).toContain('费用：暂无数据')
    expect(sanitized).toContain('坐标：暂无数据')
  })
})

// ── 验收 #62：草案 abandoned 不复活（墓碑） ──────────────────────

function draftResponse(version: number): PlanResponse {
  return { status: 'ready', final_answer: '草案', itinerary: makeItinerary({ plan_version: version }) } as PlanResponse
}

describe('clearPendingPlan — 放弃记墓碑（#62）', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
  })

  it('放弃后 pending 清空且版本进墓碑', () => {
    const withDraft = previewPlanResponse(EMPTY_PLAN_STATE, draftResponse(3))
    expect(withDraft.pending).not.toBeNull()
    const discarded = clearPendingPlan(withDraft)
    expect(discarded.pending).toBeNull()
    expect(discarded.discarded).toContain(3)
  })

  it('墓碑命中的草案经预览通道再次推送时不复活（SSE 重放/多标签写回）', () => {
    const discarded = clearPendingPlan(previewPlanResponse(EMPTY_PLAN_STATE, draftResponse(3)))
    const revived = previewPlanResponse(discarded, draftResponse(3))
    expect(revived.pending).toBeNull()
    // 新版本草案不受墓碑影响
    const fresh = previewPlanResponse(discarded, draftResponse(4))
    expect(fresh.pending?.itinerary?.plan_version).toBe(4)
  })

  it('持久化恢复时墓碑命中的 pending 被剔除（多标签页旧 state 写回场景）', () => {
    const withDraft = previewPlanResponse(EMPTY_PLAN_STATE, draftResponse(3))
    persistPlanState(withDraft)
    // 另一标签页尚不知放弃：旧 state（pending 在、无墓碑）依然可恢复
    expect(readPlanState().pending).not.toBeNull()
    // 本标签页放弃（墓碑写独立键）后，旧 state 再怎么写回都过滤
    const discarded = clearPendingPlan(withDraft)
    persistPlanState(discarded)
    sessionStorage.setItem('travel:plan-state', JSON.stringify(withDraft))
    expect(readPlanState().pending).toBeNull()
  })

  it('墓碑上限 20，防止无限增长', () => {
    let state = EMPTY_PLAN_STATE
    for (let v = 1; v <= 25; v++) {
      state = clearPendingPlan(previewPlanResponse(state, draftResponse(v)))
    }
    expect(state.discarded.length).toBe(20)
    expect(state.discarded).not.toContain(1)
    expect(state.discarded).toContain(25)
  })
})
