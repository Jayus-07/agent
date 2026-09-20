/** 错误提示：中文结论直接显示，原文收进折叠的「技术细节」（B4 拆分迁出）。
 *
 *  裸 `error.message` 常是 `{"detail": ...}` 或 SDK 英文原文，用户看不懂；但也不能直接
 *  丢掉（排障要用）。所以：**含中文就照用**，否则换成一句中文结论 + 折叠原文。
 */
export function friendlyErrorMessage(raw: string): { summary: string; raw?: string } {
  const text = raw.trim()
  if (!text) return { summary: '操作没有成功，请稍后重试' }
  if (/[\u4e00-\u9fff]/.test(text)) return { summary: text }
  return {
    summary: '请求没有成功 —— 请检查网络、网关与后端服务',
    raw: text,
  }
}

export default function ErrorNote({ message, tone = 'red' }: { message: string; tone?: 'red' | 'amber' }) {
  const { summary, raw } = friendlyErrorMessage(message)
  const palette = tone === 'amber'
    ? 'border-amber-200 bg-amber-50 text-amber-800'
    : 'border-red-200 bg-red-50 text-red-800'
  return (
    <div className={`break-words rounded-lg border px-3 py-2 text-[11px] ${palette}`}>
      <div>{summary}</div>
      {raw && (
        <details className="mt-1">
          <summary className="cursor-pointer select-none opacity-80">技术细节</summary>
          <div className="mt-0.5 break-words font-mono text-[10px] opacity-90">{raw}</div>
        </details>
      )}
    </div>
  )
}
