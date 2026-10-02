import { describe, expect, it } from 'vitest'
import {
  buildWorkbenchUrl,
  legacyRouteToWorkbench,
  resolveWorkbenchTab,
} from './workbenchRoutes'

describe('工作台 Tab 合约', () => {
  it('缺失或非法 Tab 回退到默认值，合法值原样返回', () => {
    const tabs = ['funnel', 'decision'] as const

    expect(resolveWorkbenchTab(null, tabs, 'funnel')).toBe('funnel')
    expect(resolveWorkbenchTab('unknown', tabs, 'funnel')).toBe('funnel')
    expect(resolveWorkbenchTab('decision', tabs, 'funnel')).toBe('decision')
  })

  it('构造工作台 URL 时只覆盖 tab 并保留业务筛选参数', () => {
    const params = new URLSearchParams([
      ['tab', 'old'],
      ['has_tool', 'true'],
      ['workflow_name', 'order lookup'],
      ['from', '2026-10-01T00:00:00+08:00'],
    ])

    expect(buildWorkbenchUrl('observability', 'traces', params)).toBe(
      '/observability/monitoring?tab=traces&has_tool=true&workflow_name=order+lookup&from=2026-10-01T00%3A00%3A00%2B08%3A00',
    )
  })

  it('将所有旧列表路由映射到正确工作台 Tab', () => {
    const cases = [
      ['/selection-funnel', '/selection-workbench?tab=funnel'],
      ['/selection-decision', '/selection-workbench?tab=decision'],
      ['/knowledge', '/knowledge/workbench?tab=documents'],
      ['/knowledge/documents', '/knowledge/workbench?tab=documents'],
      ['/knowledge/pending', '/knowledge/workbench?tab=pending'],
      ['/knowledge/upload-failures', '/knowledge/workbench?tab=failures'],
      ['/knowledge/keywords', '/knowledge/workbench?tab=keywords'],
      ['/knowledge/operations', '/knowledge/workbench?tab=operations'],
      ['/evaluations', '/evaluations/center?tab=results'],
      ['/evaluations/datasets', '/evaluations/center?tab=datasets'],
      ['/evaluations/feedback', '/evaluations/center?tab=feedback'],
      ['/observability', '/observability/monitoring?tab=traces'],
      ['/observability/traces', '/observability/monitoring?tab=traces'],
      ['/observability/gateway', '/observability/monitoring?tab=gateway'],
      ['/observability/tokens', '/observability/monitoring?tab=tokens'],
    ] as const

    for (const [pathname, expected] of cases) {
      expect(legacyRouteToWorkbench(pathname)).toBe(expected)
    }
  })

  it('旧 Trace URL 覆盖旧 tab 但保留其它查询参数', () => {
    expect(
      legacyRouteToWorkbench(
        '/observability/traces',
        new URLSearchParams([
          ['tab', 'old'],
          ['has_tool', '1'],
          ['workflow_name', 'checkout'],
        ]),
      ),
    ).toBe('/observability/monitoring?tab=traces&has_tool=1&workflow_name=checkout')
  })

  it('详情和未知路由不作为旧列表兼容跳转', () => {
    expect(legacyRouteToWorkbench('/observability/traces/trace-1')).toBeNull()
    expect(legacyRouteToWorkbench('/selection-decision/task-1')).toBeNull()
    expect(legacyRouteToWorkbench('/not-found')).toBeNull()
  })
})
