import WorkstationShell from '@/cs2/WorkstationShell'

// /cs/* 坐席工作台布局（P8 切换：/cs2/* → /cs/*）：新工作台壳在 root layout
// 中已排除旧 Sidebar，这里统一套壳。
export default function Cs2Layout({
  children,
}: {
  children: React.ReactNode
}) {
  return <WorkstationShell>{children}</WorkstationShell>
}
