import { redirect } from 'next/navigation'

/** 兼容旧收藏与文档链接；价格治理已并入模型与供应商页的第三个 tab。 */
export default function LegacyPriceGovernancePage() {
  redirect('/settings/models?tab=prices')
}
