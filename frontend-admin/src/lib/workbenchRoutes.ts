export const WORKBENCH_ROUTES = {
  selection: {
    path: '/selection-workbench',
    defaultTab: 'funnel',
    tabs: ['funnel', 'decision'],
  },
  knowledge: {
    path: '/knowledge/workbench',
    defaultTab: 'documents',
    tabs: ['documents', 'pending', 'failures', 'keywords', 'operations'],
  },
  evaluations: {
    path: '/evaluations/center',
    defaultTab: 'results',
    tabs: ['results', 'datasets', 'feedback'],
  },
  observability: {
    path: '/observability/monitoring',
    defaultTab: 'traces',
    tabs: ['traces', 'gateway', 'tokens'],
  },
} as const

export type WorkbenchId = keyof typeof WORKBENCH_ROUTES
export type WorkbenchTabId = (typeof WORKBENCH_ROUTES)[WorkbenchId]['tabs'][number]

const LEGACY_ROUTE_MAP: Record<string, { workbench: WorkbenchId; tab: string }> = {
  '/selection-funnel': { workbench: 'selection', tab: 'funnel' },
  '/selection-decision': { workbench: 'selection', tab: 'decision' },
  '/knowledge': { workbench: 'knowledge', tab: 'documents' },
  '/knowledge/documents': { workbench: 'knowledge', tab: 'documents' },
  '/knowledge/pending': { workbench: 'knowledge', tab: 'pending' },
  '/knowledge/upload-failures': { workbench: 'knowledge', tab: 'failures' },
  '/knowledge/keywords': { workbench: 'knowledge', tab: 'keywords' },
  '/knowledge/operations': { workbench: 'knowledge', tab: 'operations' },
  '/evaluations': { workbench: 'evaluations', tab: 'results' },
  '/evaluations/datasets': { workbench: 'evaluations', tab: 'datasets' },
  '/evaluations/feedback': { workbench: 'evaluations', tab: 'feedback' },
  '/observability': { workbench: 'observability', tab: 'traces' },
  '/observability/traces': { workbench: 'observability', tab: 'traces' },
  '/observability/gateway': { workbench: 'observability', tab: 'gateway' },
  '/observability/tokens': { workbench: 'observability', tab: 'tokens' },
}

export function resolveWorkbenchTab<TabId extends string>(
  value: string | null | undefined,
  tabIds: readonly TabId[],
  defaultTab: TabId,
): TabId {
  return value && tabIds.includes(value as TabId) ? (value as TabId) : defaultTab
}

function cloneSearchParams(searchParams?: URLSearchParams | string): URLSearchParams {
  if (!searchParams) return new URLSearchParams()
  return typeof searchParams === 'string'
    ? new URLSearchParams(searchParams.startsWith('?') ? searchParams.slice(1) : searchParams)
    : new URLSearchParams(searchParams)
}

export function buildWorkbenchUrl(
  workbench: WorkbenchId,
  tab: string,
  searchParams?: URLSearchParams | string,
): string {
  const route = WORKBENCH_ROUTES[workbench]
  const params = cloneSearchParams(searchParams)
  params.set('tab', tab)
  const query = params.toString()
  return query ? route.path + '?' + query : route.path
}

export function legacyRouteToWorkbench(
  pathname: string,
  searchParams?: URLSearchParams | string,
): string | null {
  const target = LEGACY_ROUTE_MAP[pathname]
  return target ? buildWorkbenchUrl(target.workbench, target.tab, searchParams) : null
}
