// Alerts service
export interface AlertCase { id: string; title: string; level: string; status: string; created_at: string; current_level: string; current_state: string; first_detected_at: string; product_id: string; [key: string]: any }
export interface AlertStats { total: number; critical: number; warning: number; resolved: number; info: number; [key: string]: any }
export interface AlertDetail extends AlertCase { events: AlertEvent[]; description: string; case: AlertCase }
export interface AlertEvent { id: string; action: string; timestamp: string; user: string; notified: boolean; event_type: string; created_at: string; from_state: string; to_state: string; reason: string; [key: string]: any }

const api = (url: string, opts?: RequestInit) => fetch(url, opts).then(r => r.json()).catch(() => ({}))
const BASE = '/api/observability'

export const alertService: any = {
  list: () => api(`${BASE}/alerts?limit=50`).then((d: any) => d.alerts || []),
  stats: () => api(`${BASE}/alerts?limit=1`).then((d: any) => ({ total: d.total || 0, critical: 0, warning: 0, resolved: 0, info: 0 })),
  getAlerts: () => api(`${BASE}/alerts?limit=50`).then((d: any) => d.alerts || []),
  getStats: () => api(`${BASE}/alerts?limit=1`).then((d: any) => ({ total: d.total || 0, critical: 0, warning: 0, resolved: 0, info: 0 })),
  getAlert: (id: string) => api(`${BASE}/alerts/${id}`).then((a: any) => a || { id, title: '', events: [] }),
  patchAlert: (_id: string, _data: any) => Promise.resolve(),
}
