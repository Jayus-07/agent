'use client'

import PageHeader from '@/components/layout/PageHeader'

interface Props { title: string; desc?: string; icon?: string }

export default function PlaceholderPage({ title, desc, icon = '🚧' }: Props) {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <PageHeader title={title} desc={desc} />
        <div className="bg-surface-base rounded-xl border border-border-subtle p-12 text-center">
          <div className="text-4xl mb-4">{icon}</div>
          <p className="text-sm text-text-muted">此页面正在开发中</p>
        </div>
      </div>
    </div>
  )
}
