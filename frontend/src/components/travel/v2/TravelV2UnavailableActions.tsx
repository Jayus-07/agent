export default function TravelV2UnavailableActions({ revision }: { revision: number }) {
  const assistantReasonId = 'travel-v2-assistant-unavailable'
  const shareReasonId = 'travel-v2-share-export-unavailable'

  return (
    <section
      aria-label="暂不可用的行程功能"
      className="mx-3 mt-3 rounded-xl border border-[#e3eaf3] bg-white px-4 py-3.5 lg:mx-4"
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="lg:hidden">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-[#314762]">AI 行程修改</h2>
            <span className="rounded-full bg-[#f3f6fa] px-2 py-0.5 text-xs text-[#78899e]">暂不可用</span>
          </div>
          <p id={assistantReasonId} className="mt-1.5 text-sm leading-5 text-[#71839a]">
            修改服务尚未接入。当前行程仍为 v{revision}，不会提交或改动行程。
          </p>
          <button
            type="button"
            disabled
            aria-describedby={assistantReasonId}
            className="mt-2 min-h-11 rounded-lg border border-[#dfe7f0] bg-[#f7f9fc] px-3.5 text-sm font-medium text-[#8998aa] disabled:cursor-not-allowed"
          >
            开始 AI 修改
          </button>
        </div>

        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-[#314762]">分享与导出</h2>
            <span className="rounded-full bg-[#f3f6fa] px-2 py-0.5 text-xs text-[#78899e]">暂不可用</span>
          </div>
          <p id={shareReasonId} className="mt-1.5 text-sm leading-5 text-[#71839a]">
            V2 固定版本分享和导出服务尚未接入；这里不会生成分享链接或文件。
          </p>
          <div className="mt-2 flex shrink-0 flex-wrap gap-2">
            <button
              type="button"
              disabled
              aria-describedby={shareReasonId}
              className="min-h-11 rounded-lg border border-[#dfe7f0] bg-[#f7f9fc] px-3.5 text-sm font-medium text-[#8998aa] disabled:cursor-not-allowed"
            >
              创建分享
            </button>
            <button
              type="button"
              disabled
              aria-describedby={shareReasonId}
              className="min-h-11 rounded-lg border border-[#dfe7f0] bg-[#f7f9fc] px-3.5 text-sm font-medium text-[#8998aa] disabled:cursor-not-allowed"
            >
              导出行程
            </button>
          </div>
        </div>
      </div>
    </section>
  )
}