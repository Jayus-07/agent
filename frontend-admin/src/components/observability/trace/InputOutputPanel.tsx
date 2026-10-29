import { safeDiagnosticCode } from '@/lib/observability/runtimeStatus'

interface Props {
  question: string;
  answer: string;
  answerLen?: number;
  error?: Record<string, unknown>;
}

/**
 * 问题与结果面板（2026-10-30 需求：详情页必须展示问题与结果）。
 * 内容在服务端已完成身份/资源级授权与 TRACE_DETAIL_LEVEL 脱敏
 * （full 原文 / masked PII 打码 / summary 剥离原文），本组件只渲染
 * 下发字段；空值如实显示「未采集」，不伪造占位内容。
 */
export default function InputOutputPanel({ question, answer, answerLen, error }: Props) {
  const rawErrorCode = error?.code
  const errorCode = safeDiagnosticCode(rawErrorCode)
  const hasErrorCode = typeof rawErrorCode === 'string' && rawErrorCode.trim().length > 0
  const errorMessage = typeof error?.message === 'string' ? error.message.trim() : ''
  const errorClass = typeof error?.class === 'string' ? error.class.trim() : ''
  const hasErrorInfo = Boolean(errorMessage || errorCode || hasErrorCode || errorClass)
  const trimmedAnswer = answer.trim()
  const previewTruncated = typeof answerLen === 'number'
    && answerLen > trimmedAnswer.length && trimmedAnswer.length > 0

  return (
    <section className="rounded-md border border-slate-200 bg-white p-4" aria-label="问题与结果">
      <h3 className="text-sm font-medium text-slate-800">问题与结果</h3>
      <div className="mt-3 space-y-3">
        <div>
          <p className="text-[10px] uppercase tracking-widest text-slate-400">用户问题</p>
          <p className="mt-1 whitespace-pre-wrap break-words text-sm text-slate-800">
            {question.trim() || '未采集'}
          </p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-widest text-slate-400">运行结果</p>
          <p className="mt-1 whitespace-pre-wrap break-words text-sm text-slate-800">
            {trimmedAnswer || '未采集'}
          </p>
          {previewTruncated && (
            <p className="mt-1 text-xs text-slate-400">
              存储保留前 {trimmedAnswer.length} 字预览；完整回答共 {answerLen} 字，可回源会话查看。
            </p>
          )}
        </div>
        {hasErrorInfo && (
          <div className="rounded-md border border-red-100 bg-red-50 px-3 py-2">
            <p className="text-[10px] uppercase tracking-widest text-red-400">错误信息</p>
            {errorCode && (
              <p className="mt-1 text-xs text-red-700">
                错误码：<code className="font-mono">{errorCode}</code>
              </p>
            )}
            {!errorCode && hasErrorCode && <p className="mt-1 text-xs text-red-700">错误码文本已隐藏。</p>}
            {errorClass && <p className="mt-0.5 text-xs text-red-500">错误类别：{errorClass}</p>}
            {errorMessage ? (
              <p className="mt-1 whitespace-pre-wrap break-words text-sm text-red-700">{errorMessage}</p>
            ) : (
              (errorCode || hasErrorCode) && (
                <p className="mt-1 text-xs text-red-500">错误消息未采集；可结合失败步骤与受控服务端日志排查。</p>
              )
            )}
          </div>
        )}
      </div>
    </section>
  );
}
