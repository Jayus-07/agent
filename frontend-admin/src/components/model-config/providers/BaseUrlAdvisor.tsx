/** Base URL 旁的地址助手（B4 拆分迁出）。
 *
 *  只做四件事：指出「已偏离预置」并给一键还原、指出「这个地址是另一个计费
 *  计划的端点」、指出「同域名下的收录端点」并给一键替换、指出「这个路径在
 *  目录里没有先例」。**不校验、不拦截、不替用户拍板** —— 自建网关可以用
 *  任意地址，把合法输入判成错误比漏报更糟。
 */
import type { PresetPlan, ProviderPreset } from '@/types/modelConfig'
import { AdvisorButton } from './ProbeResultDetails'
import type { BaseUrlDiagnosis } from './presets'
import { baseUrlPath } from './presets'

export default function BaseUrlAdvisor({
  diagnosis,
  busy,
  onUsePreset,
}: {
  diagnosis: BaseUrlDiagnosis
  busy: boolean
  onUsePreset: (presetId: string) => void
}) {
  if (diagnosis.kind === 'empty' || diagnosis.kind === 'custom') return null

  if (diagnosis.kind === 'matched') {
    return (
      <div data-testid="base-url-advisor" className="rounded-lg border border-emerald-200 bg-emerald-50/70 px-3 py-2 text-[11px] text-emerald-800">
        地址与预置「{diagnosis.label}」一致。
      </div>
    )
  }

  if (diagnosis.kind === 'plan-mismatch') {
    const restore = diagnosis.restore
    return (
      <div data-testid="base-url-advisor" data-kind="plan-mismatch" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] text-amber-900">
        <div className="font-medium">这个地址是「{diagnosis.hitPlanLabel}」的端点</div>
        <div className="mt-1 break-all text-amber-800">
          它与预置「{diagnosis.hitLabel}」一致，但那条属于<span className="font-medium">{diagnosis.hitPlanLabel}</span>，而当前选的是「{diagnosis.currentPlanLabel}」。
        </div>
        <div className="mt-1 text-amber-700">官方口径：按量付费与 Token Plan / Coding Plan 走的是不同端点，用错会产生额外费用。</div>
        {restore && (
          <div className="mt-2">
            <AdvisorButton disabled={busy} onClick={() => onUsePreset(restore.id)}>
              改用「{diagnosis.currentPlanLabel}」端点 · {baseUrlPath(restore.baseUrl) || '/'}
            </AdvisorButton>
          </div>
        )}
      </div>
    )
  }

  if (diagnosis.kind === 'deviated') {
    return (
      <div data-testid="base-url-advisor" data-kind="deviated" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] text-amber-900">
        <div className="font-medium">地址已偏离预置「{diagnosis.label}」</div>
        <div className="mt-1 break-all text-amber-800">预置原值是 <span className="font-mono">{diagnosis.preset.baseUrl}</span></div>
        <div className="mt-1 text-amber-700">偏离后，上方「API Key 格式」与计费口径说的仍是那条预置，可能不再适用；探测失败时请优先怀疑这个地址。</div>
        <div className="mt-2">
          <AdvisorButton disabled={busy} onClick={() => onUsePreset(diagnosis.preset.id)}>还原为预置地址</AdvisorButton>
        </div>
      </div>
    )
  }

  if (diagnosis.kind === 'suggest') {
    return (
      <div data-testid="base-url-advisor" data-kind="suggest" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] text-amber-900">
        <div className="font-medium">域名 <span className="font-mono">{diagnosis.host}</span> 在预置目录里是这些端点</div>
        <div className="mt-1 text-amber-700">同一域名的不同端点往往对应不同协议或计费计划，用错端点会产生额外费用。</div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {diagnosis.candidates.map(({ preset, label }) => (
            <AdvisorButton key={preset.id} disabled={busy} onClick={() => onUsePreset(preset.id)}>
              {label} · {baseUrlPath(preset.baseUrl) || '/'}
            </AdvisorButton>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div data-testid="base-url-advisor" data-kind="suspect" className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] text-text-secondary">
      <div className="font-medium text-text-primary">路径 <span className="font-mono">{diagnosis.path}</span> 在预置目录里没有先例</div>
      <div className="mt-1">
        收录的 {diagnosis.total} 条预置中，
        {diagnosis.samples.length > 0
          ? <>OpenAI 兼容的路径形如 {diagnosis.samples.map((item) => `「${item}」`).join('、')}。</>
          : <>没有任何一条用这种写法。</>}
        自建网关可以用任意路径，所以这不代表填错。
      </div>
      <div className="mt-1 text-text-muted">
        但若这是照厂商文档抄的<span className="font-medium">原生协议</span>地址，
        <span className="font-mono">GET {'{base}'}/models</span> 往往照样通过，直到最小调用才报「404 且响应体为空」—— 届时先回来检查这里的路径。
      </div>
    </div>
  )
}
