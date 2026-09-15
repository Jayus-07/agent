interface Props { title: string; desc?: string }

export default function PageHeader({ title, desc }: Props) {
  return (
    <div className="mb-6">
      <h1 className="text-lg font-semibold text-text-primary">{title}</h1>
      {desc && <p className="text-xs text-text-muted mt-1">{desc}</p>}
    </div>
  )
}
