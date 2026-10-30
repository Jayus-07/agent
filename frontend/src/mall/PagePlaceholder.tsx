import HealthBadge from './HealthBadge'

interface PagePlaceholderProps {
  title: string
  phase: string
  description: string
}

// P0 空页壳：标题 + 期次徽标 + 健康自检；P2 按原型补齐商城三页与购物车。
export default function PagePlaceholder({
  title,
  phase,
  description,
}: PagePlaceholderProps) {
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col justify-center p-8">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
        <span className="rounded bg-sky-50 px-2 py-0.5 text-xs font-medium text-sky-700">
          {phase}
        </span>
        <HealthBadge />
      </div>
      <p className="mt-2 text-sm text-slate-500">{description}</p>
      <div className="mt-8 rounded-lg border border-dashed border-slate-300 p-10 text-center text-sm text-slate-400">
        页面施工中 —— 交互以 prototypes/user-portal.html 为目标态
      </div>
    </div>
  )
}
