import { describe, expect, it } from 'vitest'
import {
  buildWorkbenchUrl,
  legacyRouteToWorkbench,
  resolveWorkbenchTab,
} from './workbenchRoutes'

describe('工作台 Tab 合约', () => {
  it('缺失或非法 Tab 回退到默认值，合法值原样返回', () => {
    const tabs = ['documents', 'pending'] as const

    expect(resolveWorkbenchTab(null, tabs, 'documents')).toBe('documents')
    expect(resolveWorkbenchTab('unknown', tabs, 'documents')).toBe('documents')
    expect(resolveWorkbenchTab('pending', tabs, 'documents')).toBe('pending')
  })

  it('构造工作台 URL 时只覆盖 tab 并保留筛选参数', () => {
    const params = new URLSearchParams([
      ['tab', 'old'],
      ['has_tool', 'true'],
      ['workflow_name', 'order lookup'],
    ])

    expect(buildWorkbenchUrl('observability', 'traces', params)).toBe(
      '/observability/monitoring?tab=traces&has_tool=true&workflow_name=order+lookup',
    )
  })

  it('将仍保留的旧管理端路由映射到对应工作台', () => {
    expect(legacyRouteToWorkbench('/knowledge/pending'))
      .toBe('/knowledge/workbench?tab=pending')
    expect(legacyRouteToWorkbench('/evaluations/datasets'))
      .toBe('/evaluations/center?tab=datasets')
    expect(legacyRouteToWorkbench('/observability/traces'))
      .toBe('/observability/monitoring?tab=traces')
  })

  it('不再为已退役的选品路由生成管理端跳转', () => {
    expect(legacyRouteToWorkbench('/selection-funnel')).toBeNull()
    expect(legacyRouteToWorkbench('/selection-decision')).toBeNull()
  })

  it('未知路由和详情 URL 不作为旧列表兼容跳转', () => {
    expect(legacyRouteToWorkbench('/observability/traces/trace-1')).toBeNull()
    expect(legacyRouteToWorkbench('/not-found')).toBeNull()
  })
})
