import WorkstationShell from '@/cs2/WorkstationShell'

// /cs2/* 专用布局：新工作台壳在 root layout 中已排除旧 Sidebar，这里统一套壳。
export default function Cs2Layout({
  children,
}: {
  children: React.ReactNode
}) {
  return <WorkstationShell>{children}</WorkstationShell>
}
