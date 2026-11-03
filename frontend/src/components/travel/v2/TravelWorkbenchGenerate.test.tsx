import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  createTrip: vi.fn(),
  stream: vi.fn(),
  setMessages: vi.fn(),
  messages: [] as Array<Record<string, unknown>>,
  routerReplace: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: mocks.routerReplace }),
}))
vi.mock('@/api/travel', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travel')>()),
  streamTravelPlanV2: mocks.stream,
}))
vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  createTravelTripV2: mocks.createTrip,
}))
vi.mock('@/components/travel/useTravelConversation', () => ({
  useTravelConversation: () => ({ messages: mocks.messages, setMessages: mocks.setMessages, syncError: '' }),
}))
vi.mock('@/lib/auth', () => ({ getCachedUser: () => ({ tenantId: 'tenant-a', userId: 'user-a' }) }))

import TravelWorkbenchGenerate from './TravelWorkbenchGenerate'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function planStreamMock() {
  return async function* () {
    yield {
      event: 'done',
      data: {
        status: 'success',
        result: {
          status: 'success',
          plan_status: 'confirmed',
          final_answer: '杭州行程已安排。',
          itinerary: {
            brief: { destination: '杭州', origin: '', start_date: '2026-12-01', days: 1, party_size: 1, budget_cny: null, pace: 'relaxed', preferences: [], must_go: [], avoid: [], diet: '', lodging: '', transport: '' },
            days: [{ day_index: 1, day_date: '2026-12-01', active_minutes: 90, transit_minutes: 0, cost_cny: 0, items: [{ title: '西湖', kind: 'visit', start: '09:00', end: '10:30', minutes: 90, wait_minutes: 0, note: '', poi: { poi_id: 'poi-west-lake', name: '西湖', lat: 30.25, lng: 120.15, ticket_cny: 0, source: 'amap', location_status: 'verified', required: false } }], legs: [] }],
            cost: { tickets: 0, meals: 0, lodging: 0, transit: 0, total: 0 },
            warnings: [], intercity: [],
          },
        },
      },
    }
  }
}

function mounted() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  return { container, root, unmount: () => { act(() => root.unmount()); container.remove() } }
}

describe('TravelWorkbenchGenerate（P4a 工作台生成态）', () => {
  beforeEach(() => {
    mocks.stream.mockReset()
    mocks.createTrip.mockReset()
    mocks.setMessages.mockReset().mockImplementation((update) => {
      const next = typeof update === 'function' ? update(mocks.messages) : update
      mocks.messages.splice(0, mocks.messages.length, ...next)
    })
    mocks.messages.splice(0)
    mocks.routerReplace.mockReset()
    window.history.replaceState({}, '', '/travel/itineraries/new')
    // 打字机动画依赖 rAF 帧调度，happy-dom 下时序不定——测试统一固定为
    // prefers-reduced-motion 口径（正文直显全文），断言不依赖动画帧
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('prefers-reduced-motion: reduce'),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }))
  })

  afterEach(() => {
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    window.localStorage.clear()
    document.body.innerHTML = ''
  })

  it('空态渲染三列骨架（生成面板引导 + 草稿概览空态 + 对话伴随）', async () => {
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '杭州两日慢游' }))
        await Promise.resolve()
      })
      expect(view.container.textContent).toContain('正在生成新行程')
      expect(view.container.textContent).toContain('还没有草稿')
      expect(view.container.textContent).toContain('AI 旅行助手')
      // initialPrompt 进入对话输入框（首页携 prompt 直达）
      const textarea = view.container.querySelector<HTMLTextAreaElement>('#workbench-generate-input')!
      expect(textarea.value).toBe('杭州两日慢游')
    } finally {
      view.unmount()
    }
  })

  it('生成完成自动保存为正式行程（幂等）并 replace 进工作台，不再有确认按钮', async () => {
    // 2026-11-03 用户口径：保存为正式行程/再调整一下按钮取消——保存动作
    // 在草稿就绪时自动发生（幂等键），调整在工作台继续（修订/撤销/删除）。
    mocks.stream.mockImplementation(planStreamMock())
    mocks.createTrip.mockImplementation(async (input: { title: string; document: { schema_version: number } }) => ({
      trip_id: 'v2-trip-wb', title: input.title, status: 'active', revision: 1,
      document: input.document, source_template_id: null, source_template_version: null,
      created_at: '2026-11-02T00:00:00Z', updated_at: '2026-11-02T00:00:00Z',
      conversation_id: 'conv-server-1',
    }))
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '杭州一日游' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      // 自动保存：无需点击任何按钮即落库（幂等键由前端生成）
      expect(mocks.createTrip).toHaveBeenCalledOnce()
      expect(mocks.createTrip.mock.calls[0][0].document.schema_version).toBe(2)
      // 保存成功 → replace 进正式工作台（服务端会话关联为权威）
      expect(mocks.routerReplace).toHaveBeenCalledWith('/travel/itineraries/v2-trip-wb?conversation_id=conv-server-1')
      // 确认按钮族已下线（页头副标题含「保存为正式行程」文案，按按钮断言）
      const text = view.container.textContent || ''
      expect([...view.container.querySelectorAll('button')].some((button) => button.textContent === '保存为正式行程')).toBe(false)
      expect([...view.container.querySelectorAll('button')].some((button) => button.textContent === '再调整一下')).toBe(false)
    } finally {
      view.unmount()
    }
  })

  it('保存窗口内草稿预览按原型渲染摘要卡与富卡片（评分/门票/来源/交通行）', async () => {
    // 原型规划页形态：中列顶部结构化摘要卡（住宿建议/预算求解/天气风险）+
    // 逐日富卡片（评分/来源/门票/地址标签）+ 卡片间交通行——数据全部来自
    // 草稿 document，不新增数据源。createTrip 挂起以留在保存窗口内断言。
    mocks.stream.mockImplementation(async function* () {
      yield {
        event: 'done',
        data: {
          status: 'success',
          result: {
            status: 'success',
            plan_status: 'confirmed',
            final_answer: '福州行程已安排。',
            itinerary: {
              brief: { destination: '福州', origin: '', start_date: '2026-12-01', days: 1, party_size: 2, budget_cny: null, pace: 'relaxed', preferences: [], must_go: [], avoid: [], diet: '', lodging: '', transport: '' },
              days: [{
                day_index: 1, day_date: '2026-12-01', active_minutes: 150, transit_minutes: 0, cost_cny: 40,
                items: [
                  { title: '三坊七巷', kind: 'visit', start: '09:00', end: '10:30', minutes: 90, wait_minutes: 0, note: '', poi: { poi_id: 'poi-sfq', name: '三坊七巷', lat: 26.08, lng: 119.30, ticket_cny: 0, source: 'amap.live', location_status: 'verified', required: false, address: '福州市鼓楼区南后街', rating: 4.8 } },
                  { title: '烟台山', kind: 'visit', start: '11:00', end: '12:00', minutes: 60, wait_minutes: 0, note: '', poi: { poi_id: 'poi-yts', name: '烟台山', lat: 26.05, lng: 119.31, ticket_cny: 40, source: 'amap.live', location_status: 'estimated', required: false } },
                ],
                legs: [{ from_title: '三坊七巷', to_title: '烟台山', minutes: 8, distance_km: 1.2, mode: 'walk', cost_cny: 0, source: 'tencentlbs' }],
              }],
              cost: { tickets: 40, meals: 0, lodging: 0, transit: 0, total: 40 },
              warnings: [], intercity: [],
            },
          },
        },
      }
    })
    mocks.createTrip.mockImplementation(() => new Promise(() => {}))
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '福州一日游' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      // 自动保存已发起（挂起中），预览可见
      expect(mocks.createTrip).toHaveBeenCalledOnce()
      const text = view.container.textContent || ''
      expect(text).toContain('正在保存为正式行程')
      expect(text).toContain('自动保存中')
      // 摘要卡（缺数据如实显示，不伪造）
      expect(text).toContain('住宿建议')
      expect(text).toContain('未安排 —— 对话或备选确认后选入')
      expect(text).toContain('预算求解')
      expect(text).toContain('天气与风险')
      expect(text).toContain('出行日期已提供，临近出发自动联动预报')
      // 富卡片：评分/免费门票/收费门票/来源标签/地址
      expect(text).toContain('评分 4.8')
      expect(text).toContain('门票 · 免费')
      expect(text).toContain('门票 ¥40')
      expect(text).toContain('amap.live')
      expect(text).toContain('福州市鼓楼区南后街')
      // 交通行（原型：交通·步行·8 分钟）
      expect(text).toContain('交通 · 步行 · 8 分钟')
    } finally {
      view.unmount()
    }
  })

  // —— P4b 测试迁移（自 TravelChatPage.test）：生成引擎语义的回归网随
  // chat 页退役迁入本文件，断言语义保持不变（保存成功后入口断言按
  // WorkbenchGenerate 语义改为 routerReplace）。 ——

  it('草稿创建回执丢失时保持 UNKNOWN，安全重试复用原键和完全相同 payload', async () => {
    mocks.createTrip
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockImplementationOnce(async (input: { title: string; document: { schema_version: number; days: unknown[] } }) => ({
        trip_id: 'v2-trip-replayed', title: input.title, status: 'active', revision: 1,
        document: input.document, source_template_id: null, source_template_version: null,
        created_at: '2026-11-02T00:00:00Z', updated_at: '2026-11-02T00:00:00Z',
      } as never))
    mocks.stream.mockImplementation(planStreamMock())
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '帮我安排杭州一日游' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        // 自动保存异步失败→UNKNOWN 落 UI：轮询等待重试按钮出现
        for (let i = 0; i < 30 && !view.container.textContent?.includes('使用同一请求安全重试'); i++) {
          await new Promise((resolve) => setTimeout(resolve, 10))
        }
      })
      // 自动保存首轮失败（回执丢失）→ UNKNOWN，无需点击保存按钮
      expect(mocks.createTrip).toHaveBeenCalledOnce()
      expect(view.container.textContent).toContain('状态待核实')
      expect(view.container.textContent).toContain('服务端可能已经完成操作')
      const originalRequest = mocks.createTrip.mock.calls[0]
      const retry = [...view.container.querySelectorAll('button')].find((button) => button.textContent === '使用同一请求安全重试')!
      await act(async () => {
        retry.click()
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      // 红线：UNKNOWN 安全重试必须复用同一幂等键与完全相同 payload，不得另起请求
      expect(mocks.createTrip).toHaveBeenCalledTimes(2)
      expect(mocks.createTrip.mock.calls[1]).toEqual(originalRequest)
      const replacedHref = mocks.routerReplace.mock.calls.at(-1)?.[0] as string
      expect(replacedHref.startsWith('/travel/itineraries/v2-trip-replayed?conversation_id=')).toBe(true)
      expect(view.container.textContent).toContain('行程已保存到 V2')
    } finally {
      view.unmount()
    }
  })

  it('澄清结果渲染结构化选项，点击选项以该内容继续对话', async () => {
    // 第一次调用返回澄清；第二次回落到 plan generator（点击选项后进草稿预览）
    mocks.stream.mockImplementationOnce(async function* () {
      yield {
        event: 'done',
        data: {
          status: 'needs_clarification',
          result: {
            status: 'needs_clarification',
            result_kind: 'clarification',
            final_answer: '想去几天？',
            clarification: '想去几天？',
            clarification_options: [
              { label: '两天', days: 2, message: '两天行程' },
              { label: '三天', days: 3, message: '三天行程' },
            ],
          },
        },
      }
    }).mockImplementation(planStreamMock())
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '帮我安排行程' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      expect(view.container.textContent).toContain('需要补充信息')
      const optionTwo = [...view.container.querySelectorAll('button')].find((button) => button.textContent === '两天')!
      expect(optionTwo).toBeTruthy()
      await act(async () => {
        optionTwo.click()
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      // 选项点击即以该内容发送；第二次 stream 调用走默认 plan 分支进入草稿预览
      expect(mocks.setMessages.mock.calls.some(([update]) => {
        const next = typeof update === 'function' ? update(mocks.messages) : update
        return next.some((message: { role?: string; text?: string }) => message.role === 'user' && message.text === '两天')
      })).toBe(true)
      expect(view.container.textContent).toContain('正在保存为正式行程')
    } finally {
      view.unmount()
    }
  })

  it('渲染 tool.card 查询结果卡，动作直通聊天管线', async () => {
    // 第一轮：tool.card 事件 + 纯问答结果（无 itinerary → answer）
    mocks.stream.mockImplementationOnce(async function* () {
      yield {
        event: 'tool.card',
        data: {
          card: {
            kind: 'food', title: '', provider: 'amap', disclosure: '价格为查询时点快照',
            items: [{ id: 'm-1', name: '厦门沙茶面', category: '闽菜', address: '中山路 1 号', rating: 4.5 }],
          },
        },
      }
      yield {
        event: 'done',
        data: { status: 'success', result: { status: 'success', final_answer: '找到几家本地餐馆。' } },
      }
    }).mockImplementation(planStreamMock())
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '今晚吃什么' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      expect(view.container.textContent).toContain('餐饮推荐')
      expect(view.container.textContent).toContain('厦门沙茶面')
      expect(view.container.textContent).toContain('价格为查询时点快照')

      // 卡片动作直通既有聊天管线：把条目作为新消息发送（第二轮 stream）
      const askButton = [...view.container.querySelectorAll('button')].find((button) => button.textContent?.includes('帮我排进行程'))!
      expect(askButton).toBeTruthy()
      await act(async () => {
        askButton.click()
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      expect(mocks.setMessages.mock.calls.some(([update]) => {
        const next = typeof update === 'function' ? update(mocks.messages) : update
        return next.some((message: { role?: string; text?: string }) => message.role === 'user' && message.text?.includes('厦门沙茶面'))
      })).toBe(true)
      expect(mocks.stream).toHaveBeenCalledTimes(2)
    } finally {
      view.unmount()
    }
  })

  it('持久化待决令牌但不显示给用户，刷新后随确认自动提交', async () => {
    const token = 'a'.repeat(24)
    mocks.stream.mockImplementationOnce(async function* () {
      yield {
        event: 'done',
        data: {
          status: 'needs_clarification',
          result: {
            status: 'needs_clarification',
            result_kind: 'clarification',
            final_answer: '请回复保留或移除指定地点。',
            itinerary: null,
            travel_context: { pending_decision: { decision_token: token } },
          },
        },
      }
    })
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '规划福州一日游' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      const updates = mocks.setMessages.mock.calls.map(([update]) => update)
      const storedMessages = updates.reduce<Array<{ role: string; text: string }>>(
        (current, update) => typeof update === 'function' ? update(current) : update,
        [],
      )
      const pendingMessage = storedMessages.find((message: { role: string; text: string }) =>
        message.role === 'assistant')
      expect(pendingMessage?.text).toContain(`[[travel-decision-token:${token}]]`)
      expect(view.container.textContent).not.toContain('travel-decision-token')
    } finally {
      view.unmount()
    }

    // 模拟刷新恢复：会话消息（含令牌）由服务端历史恢复后重新进入生成态
    mocks.messages.splice(0, mocks.messages.length, {
      id: 'server:pending', conversationId: 'travel-conversation', turnId: 'turn-1',
      role: 'assistant', text: `请回复保留。\n[[travel-decision-token:${token}]]`,
    })
    mocks.stream.mockImplementationOnce(async function* () {
      yield {
        event: 'done',
        data: { status: 'answered', result: { status: 'answered', final_answer: '已处理。', itinerary: null } },
      }
    })
    const resumed = mounted()
    try {
      await act(async () => {
        resumed.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '保留' }))
        await Promise.resolve()
      })
      await act(async () => {
        resumed.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      expect(mocks.stream.mock.calls.at(-1)?.[2].actionPayload).toEqual({ decision_token: token })
      expect(resumed.container.textContent).not.toContain('travel-decision-token')
    } finally {
      resumed.unmount()
    }
  })

  it('生成过程行进气泡：运行中单行替换显示阶段，结束折叠为已完成摘要（C1）', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    mocks.stream.mockImplementation(async function* (_text: unknown, _conversationId: unknown, options: { clientRunId: string }) {
      const rid = options.clientRunId
      yield { event: 'stage.started', data: { run_id: rid, seq: 1, stage: 'travel_slot_filler' } }
      await gate
      yield { event: 'stage.finished', data: { run_id: rid, seq: 2, stage: 'travel_slot_filler' } }
      yield { event: 'tool.started', data: { run_id: rid, seq: 3, tool: 'travel.weather.query', tool_call_id: 'tc-1' } }
      yield { event: 'tool.result', data: { run_id: rid, seq: 4, tool: 'travel.weather.query', tool_call_id: 'tc-1', status: 'success', duration_ms: 214 } }
      yield { event: 'done', data: { run_id: rid, seq: 5, status: 'success', result: { status: 'success', final_answer: '杭州两日行程已生成。' } } }
    })
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '杭州两日' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      // 运行中：过程行嵌在助手气泡内，按后端 stage 名映射原型中文标签
      expect(view.container.textContent).toContain('正在槽位补全…')

      await act(async () => {
        release()
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      const text = view.container.textContent || ''
      // 结束后折叠摘要随正式回复气泡常驻（原型 tp-proc-fold），Tool 耗时入展开详情
      expect(text).toContain('✓ 已完成 1 个步骤')
      expect(text).toContain('杭州两日行程已生成。')
      // 原型 tp-toolbox：Tool 状态箱展示本轮查询工具与耗时
      expect(text).toContain('查询过程 · Tool 状态')
      expect(text).toContain('travel.weather.query')
    } finally {
      view.unmount()
    }
  })

  it('助手回复按 markdown 渲染标题/加粗/列表，不裸奔语法符号（C3）', async () => {
    mocks.stream.mockImplementation(async function* () {
      yield {
        event: 'done',
        data: { status: 'success', result: { status: 'success', final_answer: '# 杭州 2 天行程\n\n**人数** 3 人\n\n- 候选池：西湖\n- 备选：灵隐寺' } },
      }
    })
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '杭州两日' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      // h1 按主聊天契约降级为 h2 渲染；加粗/列表结构化
      const heading = [...view.container.querySelectorAll('h2')].find((node) => node.textContent === '杭州 2 天行程')
      expect(heading).toBeTruthy()
      const bold = [...view.container.querySelectorAll('strong')].find((node) => node.textContent === '人数')
      expect(bold).toBeTruthy()
      const markdownList = heading!.parentElement?.parentElement?.querySelectorAll('li') ?? []
      expect(markdownList.length).toBeGreaterThanOrEqual(2)
      expect(view.container.textContent).not.toContain('# 杭州')
      expect(view.container.textContent).not.toContain('**人数**')
    } finally {
      view.unmount()
    }
  })

  it('助手列呈现 Tool 状态箱与放大对话，不渲染 banner 与快捷查询按钮（2026-11-03 用户口径）', async () => {
    mocks.stream.mockImplementation(planStreamMock())
    // createTrip 挂起：停留在自动保存窗口内断言页面骨架
    mocks.createTrip.mockImplementation(() => new Promise(() => {}))
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '杭州一日游' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      const text = view.container.textContent || ''
      // 2026-11-03 用户口径：banner 与酒店/美食/动车三按钮不上对话窗
      expect(text).not.toContain('查询结果不会自动加入正式行程')
      expect([...view.container.querySelectorAll('button')].some((button) => button.textContent === '酒店')).toBe(false)
      // Tool 状态箱与放大对话保留
      expect(text).toContain('查询过程 · Tool 状态')
      // 自动保存口径：页头「保存中」，确认按钮族已下线
      expect(text).toContain('保存中')
      expect(text).toContain('自动保存中')
      expect([...view.container.querySelectorAll('button')].some((button) => button.textContent === '保存为正式行程')).toBe(false)
      expect([...view.container.querySelectorAll('button')].some((button) => button.textContent === '再调整一下')).toBe(false)
      // 放大对话切换（原型 ws-chatexp）
      const expand = [...view.container.querySelectorAll('button')].find((button) => button.textContent === '放大对话')!
      await act(async () => {
        expand.click()
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      expect(view.container.textContent).toContain('还原对话')
    } finally {
      view.unmount()
    }
  })

  it('输入框回车即发送，Shift+Enter 不发送（换行留给文本）', async () => {
    mocks.stream.mockImplementation(planStreamMock())
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '杭州两日' }))
        await Promise.resolve()
      })
      const textarea = view.container.querySelector<HTMLTextAreaElement>('#workbench-generate-input')!
      await act(async () => {
        textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      expect(mocks.stream).toHaveBeenCalledTimes(1)
      await act(async () => {
        textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      expect(mocks.stream).toHaveBeenCalledTimes(1)
    } finally {
      view.unmount()
    }
  })
})
