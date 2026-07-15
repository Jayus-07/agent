import { MOCK_TOKEN } from '@/services/mock/trace'

export default function TokenInfo() {
  const t = MOCK_TOKEN
  return (
    <div className="flex items-center gap-3 mt-1.5 text-[10px] text-text-muted">
      <span>输入 {t.inputTokens.toLocaleString()}</span>
      <span>输出 {t.outputTokens.toLocaleString()}</span>
      <span>共计 {t.totalTokens.toLocaleString()} tokens</span>
      <span className="ml-auto">{t.latency.toFixed(1)}s</span>
      <span>${t.cost.toFixed(3)}</span>
    </div>
  )
}
