import { safeDiagnosticCode } from '@/lib/observability/runtimeStatus'

interface Props {
  question: string;
  answer: string;
  error?: Record<string, unknown>;
}

/** Run 详情不会在默认界面暴露原始请求、响应或错误文本。 */
export default function InputOutputPanel({ error }: Props) {
  const rawErrorCode = error?.code
  const errorCode = safeDiagnosticCode(rawErrorCode)
  const hasErrorCode = typeof rawErrorCode === 'string' && rawErrorCode.trim().length > 0

  return (
    <section className="rounded-md border border-slate-200 bg-white p-4" aria-label="隐私保护">
      <h3 className="text-sm font-medium text-slate-800">输入与输出内容已保护</h3>
      <p className="mt-1 text-sm text-slate-600">
        请求、模型回答及错误消息可能包含个人信息或业务数据，此视图仅展示执行状态与诊断元数据。
      </p>
      {errorCode && (
        <p className="mt-3 text-xs text-red-700">
          错误码：<code className="font-mono">{errorCode}</code>
        </p>
      )}
      {!errorCode && hasErrorCode && <p className="mt-3 text-xs text-red-700">错误码文本已隐藏。</p>}
    </section>
  );
}
