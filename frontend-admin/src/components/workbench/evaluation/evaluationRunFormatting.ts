export function formatEvaluationRunCost(
  amount: number | string | null | undefined,
  currency: string | null | undefined,
): string {
  if (amount === null || amount === undefined) return '未归因'
  return `${amount} ${currency?.trim() || '币种未知'}`
}
