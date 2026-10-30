import HealthBadge from './HealthBadge'

interface PagePlaceholderProps {
  title: string
  phase: string
  description: string
}

// P0 空页壳：标题 + 期次徽标 + 健康自检；各期按原型逐页替换实现。
export default function PagePlaceholder({
  title,
  phase,
  description,
}: PagePlaceholderProps) {
  return (
    <div className="p-8">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
        <span className="rounded bg-sky-50 px-2 py-0.5 text-xs font-medium text-sky-700">
          {phase}
        </span>
        <HealthBadge />
      </div>
      <p className="mt-2 max-w-2xl text-sm text-slate-500">{description}</p>
      <div className="mt-8 rounded-lg border border-dashed border-slate-300 p-10 text-center text-sm text-slate-400">
        页面施工中 —— 交互以 prototypes/agent-workstation.html 为目标态
      </div>
    </div>
  )
}
