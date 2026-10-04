interface Props { title: string; desc?: string; className?: string }

export default function PageHeader({ title, desc, className }: Props) {
  return (
    <div className={className ?? 'mb-6'}>
      <h1 className="text-lg font-semibold text-text-primary">{title}</h1>
      {desc && <p className="text-xs text-text-muted mt-1">{desc}</p>}
    </div>
  )
}
