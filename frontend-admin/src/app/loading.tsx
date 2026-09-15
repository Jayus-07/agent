export default function Loading() {
  return (
    <div className="h-full flex items-center justify-center" aria-busy="true" aria-label="页面加载中">
      <div className="flex flex-col items-center gap-3">
        <div className="w-7 h-7 rounded-full border-2 border-black/10 border-t-accent animate-spin" />
        <span className="text-xs text-text-muted">加载中…</span>
      </div>
    </div>
  )
}
