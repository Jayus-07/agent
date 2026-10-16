import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import ModelPriceMonitorTab from './ModelPriceMonitorTab'
import type { ModelPriceMonitorRun } from '@/api/modelPrices'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

function mount(run: ModelPriceMonitorRun) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted.push({ container, root })
  act(() => root.render(<ModelPriceMonitorTab
    runs={[run]}
    schedule={{ timezone: 'Asia/Shanghai', hour: 8, minute: 0 }}
    isLoading={false}
    isError={false}
    isFetching={false}
    onRefresh={vi.fn()}
  />))
  return container
}

afterEach(() => {
  mounted.splice(0).forEach(({ container, root }) => {
    act(() => root.unmount())
    container.remove()
  })
})

describe('模型价格巡检管理视图', () => {
  it('展示执行状态、来源、系统价格差异和告警记录', () => {
    const run: ModelPriceMonitorRun = {
      run_id: 'run-1',
      observed_on: '2026-10-16',
      status: 'partial',
      started_at: '2026-10-16T08:00:00+08:00',
      finished_at: '2026-10-16T08:00:04+08:00',
      sources_total: 3,
      sources_failed: 1,
      quotes_recorded: 1,
      source_changes: 0,
      system_mismatches: 1,
      source_results: [{
        source_id: 'aliyun_qwen3_7_text_rerank',
        name: '阿里云 Qwen3.7 Text Rerank',
        url: 'https://help.aliyun.com/en/model-studio/qwen3-7-text-rerank',
        status: 'succeeded',
        quote_count: 1,
        quotes: [],
      }],
      comparison_results: {
        prices: [{
          model_name: 'qwen3.7-text-rerank',
          component: 'rerank',
          dimension: 'input',
          source_id: 'aliyun_qwen3_7_text_rerank',
          source_url: 'https://help.aliyun.com/en/model-studio/qwen3-7-text-rerank',
          source_updated_at: '',
          content_sha256: 'a'.repeat(64),
          official: { price_per_unit: '0.5', unit: 'per_1m_tokens', currency: 'CNY' },
          system: { price_per_unit: '0.069440', unit: 'per_1m_tokens', currency: 'USD' },
          comparison_currency: 'CNY',
          official_price_cny: '0.5',
          system_price_cny: '0.49996800',
          official_fx_to_cny: '1',
          system_fx_to_cny: '7.20',
          comparison_tolerance_cny: '0.00000360',
          matches_system: false,
        }],
        source_changes: [],
        system_mismatches: [],
      },
      alert_events: [{ code: 'MODEL_PRICE_SYSTEM_MISMATCH', recorded: true, delivery: 'unknown' }],
      error_type: null,
    }
    const container = mount(run)

    expect(container.textContent).toContain('部分完成')
    expect(container.textContent).toContain('价格差异')
    act(() => {
      const button = Array.from(container.querySelectorAll('button')).find((item) => item.textContent?.includes('查看详情'))
      button?.click()
    })

    expect(container.textContent).toContain('官方价格')
    expect(container.textContent).toContain('CNY 0.5 / 1M tokens')
    expect(container.textContent).toContain('USD 0.069440 / 1M tokens')
    expect(container.textContent).toContain('折算核对：官方 CNY 0.5 · 系统 CNY 0.49996800')
    expect(container.textContent).toContain('汇率 1 USD = 7.20 CNY')
    expect(container.textContent).toContain('官方价与系统价格不一致')
    expect(container.textContent).toContain('Webhook 实际送达状态不在此处确认')
  })

  it('显示 Celery revoke 收敛后的取消状态', () => {
    const run: ModelPriceMonitorRun = {
      run_id: 'run-cancelled',
      observed_on: '2026-10-16',
      status: 'cancelled',
      started_at: '2026-10-16T08:00:00+08:00',
      finished_at: '2026-10-16T08:00:01+08:00',
      sources_total: 3,
      sources_failed: 0,
      quotes_recorded: 0,
      source_changes: 0,
      system_mismatches: 0,
      source_results: [],
      comparison_results: {},
      alert_events: [],
      error_type: 'TaskRevokedError',
    }

    expect(mount(run).textContent).toContain('已取消')
  })
})
