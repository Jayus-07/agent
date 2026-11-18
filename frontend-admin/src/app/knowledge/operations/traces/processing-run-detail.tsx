import type { KnowledgeDoc, ProcessingRunDetail } from "@/api/knowledge";
import {
  getAdminErrorLabel,
  getCacheStatusLabel,
  getFallbackTypeLabel,
  getFileFormatLabel,
  getModelRoleLabel,
  getOperationLabel,
  getProcessingCodeLabel,
  getProcessingStageLabel,
  getProcessingStatusLabel,
  getProviderLabel,
} from "@/lib/knowledge-display";

function formatRecordedTime(value?: string): string {
  if (!value) return "未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function showRecordedNumber(value?: number): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString()
    : "未记录";
}

export default function ProcessingRunDetailView({
  detail,
  documentName,
  expectedDocId = "",
  expectedFileHash = "",
  registryDocument,
  registryIdentityError = "",
  runIdentityError = "",
  historyFallback = false,
  onBack,
  backLabel = "文档",
}: {
  detail: ProcessingRunDetail;
  documentName: string;
  expectedDocId?: string;
  expectedFileHash?: string;
  registryDocument?: KnowledgeDoc | null;
  registryIdentityError?: string;
  runIdentityError?: string;
  historyFallback?: boolean;
  onBack?: () => void;
  backLabel?: string;
}) {
  const steps = detail.steps ?? [];
  const usage = detail.usage ?? [];
  const registryDocId = String(registryDocument?.doc_id || registryDocument?.id || "");
  const registryName = String(registryDocument?.file_name || registryDocument?.name || "");
  const registryFileHash = String(
    registryDocument?.file_hash || registryDocument?.hash || registryDocument?.sha256 || "",
  );
  const unpublishedCandidate = Boolean(
    detail.publication_status === "unpublished"
    && detail.document_registered === false,
  );
  const registeredRunIdentityVerified = Boolean(
    expectedDocId
    && detail.doc_id === expectedDocId
    && registryDocId === expectedDocId
    && registryName
    && expectedFileHash
    && String(detail.file_hash || "").toLowerCase() === expectedFileHash.toLowerCase()
    && registryFileHash.toLowerCase() === expectedFileHash.toLowerCase()
    && !registryIdentityError
    && !runIdentityError,
  );
  const unpublishedRunIdentityVerified = Boolean(
    unpublishedCandidate
    && expectedDocId
    && detail.doc_id === expectedDocId
    && expectedFileHash
    && String(detail.file_hash || "").toLowerCase() === expectedFileHash.toLowerCase()
    && !runIdentityError,
  );
  const runIdentityVerified = registeredRunIdentityVerified || unpublishedRunIdentityVerified;
  const identityWarnings = [
    unpublishedCandidate ? "" : registryIdentityError,
    runIdentityError,
    !expectedDocId ? "入口未提供文档 ID" : "",
    !expectedFileHash ? "入口未提供文件 SHA-256，无法核对版本" : "",
    !registryName && !unpublishedCandidate ? "文档登记未提供可核对的文件名" : "",
    !registryFileHash && !unpublishedCandidate ? "文档登记未记录文件 SHA-256" : "",
  ].filter(Boolean);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-8 space-y-5">
        {onBack && (
          <button onClick={onBack} className="text-xs text-slate-400 hover:text-slate-600">
            ← 返回{backLabel}
          </button>
        )}
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
          <div className="text-sm font-semibold text-amber-900">
            {unpublishedCandidate ? "处理运行记录（未发布）" : "处理运行记录（非 Trace）"}
          </div>
          <p className="mt-1 text-xs leading-relaxed text-amber-800">
            {unpublishedCandidate
              ? "本次处理没有发布活动文档；页面用运行记录里的文档 ID 与文件 SHA-256 核验来源。"
              : "这是数据库保存的处理运行和阶段记录，用来补看未持久化或已过期的 Trace。它不包含未保存的 Span，不能据此还原 Trace 中没有记录的内容。"}
          </p>
        </div>

        {historyFallback && (
          <div role="status" className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-xs leading-relaxed text-blue-800">
            文件列表没有可用的最近阶段记录；以下记录按文件 SHA-256 从历史运行中找到，内容与当前文件一致。
          </div>
        )}

        <div>
          <h1 className="text-base font-semibold text-slate-800">{registryName || documentName || "未记录文件名"}</h1>
          <p className="mt-1 break-all font-mono text-[10px] text-slate-400">运行 ID：{detail.run_id}</p>
        </div>

        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-xs font-medium text-slate-600">文件与处理运行身份</h2>
            <span
              role="status"
              className={`rounded px-2 py-1 text-[10px] font-semibold ${runIdentityVerified
                ? "bg-emerald-50 text-emerald-700"
                : "bg-amber-50 text-amber-800"}`}
            >
              {runIdentityVerified
                ? (unpublishedCandidate ? "运行身份核验通过（未发布）" : "文件与运行身份核验通过")
                : "文件身份未核验"}
            </span>
          </div>
          <dl className="grid grid-cols-1 gap-x-5 gap-y-3 sm:grid-cols-2">
            {[
              ["文档登记文件名", registryName || (unpublishedCandidate ? "未登记（未发布）" : "未核验")],
              ["入口文件名", documentName || "未记录"],
              ["入口文档 ID", expectedDocId || "未记录"],
              ["登记文档 ID", registryDocId || (unpublishedCandidate ? "未登记（未发布）" : "未核验")],
              ["运行文档 ID", detail.doc_id || "未记录"],
              ["入口文件 SHA-256", expectedFileHash || "未记录"],
              ["登记文件 SHA-256", registryFileHash || (unpublishedCandidate ? "未登记（未发布）" : "未核验")],
              ["运行文件 SHA-256", detail.file_hash || "未记录"],
            ].map(([label, value]) => (
              <div key={label} className="min-w-0">
                <dt className="text-[10px] text-slate-400">{label}</dt>
                <dd className="mt-0.5 break-all font-mono text-xs text-slate-700">{value}</dd>
              </div>
            ))}
          </dl>
          {identityWarnings.length > 0 && (
            <p role="alert" className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800">
              未核验原因：{identityWarnings.join("；")}
            </p>
          )}
        </section>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {[
            ["状态", getProcessingStatusLabel(detail.status)],
            ["操作", getOperationLabel(detail.operation)],
            ["处理流水线版本", detail.pipeline_version || "未记录"],
            ["文档 ID", detail.doc_id || "未记录"],
            ["文件 SHA-256", detail.file_hash || "未记录"],
            ["Task ID", detail.task_id || "未记录"],
            ["开始时间", formatRecordedTime(detail.started_at)],
            ["结束时间", formatRecordedTime(detail.finished_at)],
            ["Trace ID", detail.trace_id || "未记录"],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border border-slate-200 bg-white px-3 py-2">
              <div className="text-[10px] text-slate-400">{label}</div>
              <div className="mt-1 break-all text-xs font-medium text-slate-700">{value}</div>
            </div>
          ))}
        </div>

        {detail.error_message && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
            运行错误：{getAdminErrorLabel(detail.error_message)}
          </div>
        )}

        {!!detail.model_summary?.length && (
          <section className="rounded-xl border border-slate-200 bg-white p-5">
            <h2 className="mb-3 text-xs font-medium text-slate-600">本次实际使用模型</h2>
            <div className="flex flex-wrap gap-2">
              {detail.model_summary.map((model, index) => (
                <span key={`${model.stage || model.role || "model"}-${index}`} className="rounded-full bg-violet-50 px-2.5 py-1 text-[10px] text-violet-700">
                  {model.stage ? getProcessingStageLabel(model.stage) : getModelRoleLabel(model.role)}：{model.provider ? "供应商 " + getProviderLabel(String(model.provider)) + " / " : ""}{String(model.model_name || "未记录")}
                </span>
              ))}
            </div>
          </section>
        )}

        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          <div className="border-b border-slate-100 px-5 py-3">
            <h2 className="text-xs font-medium text-slate-600">处理阶段（{steps.length}）</h2>
          </div>
          {steps.length === 0 ? (
            <p className="px-5 py-8 text-center text-xs text-slate-400">这次运行没有保存阶段明细。</p>
          ) : (
            <div className="divide-y divide-slate-100">
              {steps.map((step, index) => (
                <article key={step.step_id || `${step.stage}-${index}`} className="px-5 py-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-medium text-slate-800">
                      {getProcessingStageLabel(step.stage)}
                    </h3>
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">
                      {getProcessingStatusLabel(step.status)}
                    </span>
                    {step.duration_ms != null && (
                      <span className="ml-auto font-mono text-[10px] text-slate-400">
                        {step.duration_ms} ms
                      </span>
                    )}
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[10px] text-slate-500 sm:grid-cols-3">
                    <div>引擎：{getModelRoleLabel(step.engine_type)}</div>
                    <div>模型：{step.model_name ? (step.provider ? getProviderLabel(String(step.provider)) + " / " : "") + step.model_name : "未记录/未调用"}</div>
                    <div>输入/输出：{showRecordedNumber(step.input_count)} / {showRecordedNumber(step.output_count)}</div>
                    <div>词元数：{showRecordedNumber(step.total_tokens)}</div>
                    <div>模型版本：{step.model_revision || "未记录"}</div>
                    <div>提示词版本：{step.prompt_version || "未记录"}</div>
                    <div>配置来源：{step.config_source || "未记录"}</div>
                    <div>规则版本：{step.rules_version || "未记录"}</div>
                    <div>分类体系版本：{step.taxonomy_version || "未记录"}</div>
                    <div>数据结构指纹：{step.schema_fingerprint || "未记录"}</div>
                    {step.cache_status && <div>缓存：{getCacheStatusLabel(step.cache_status)}</div>}
                  </div>
                  {(step.skip_reason || step.fallback_reason) && (
                    <p className="mt-2 text-[10px] text-slate-500">
                      {step.skip_reason && <span>跳过原因：{getProcessingCodeLabel(step.skip_reason)} </span>}
                      {step.fallback_reason && <span>{step.stage === "chunking_llm_fallback" ? "触发原因：" : "降级原因："}{getProcessingCodeLabel(step.fallback_reason)}</span>}
                    </p>
                  )}
                  {step.stage === "chunking_llm_fallback" && (
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 rounded-md bg-violet-50 px-3 py-2 text-[10px] text-violet-800">
                      <span>格式：{getFileFormatLabel(String(step.metadata?.source_format || ""))}</span>
                      <span>类型：{getFallbackTypeLabel(String(step.metadata?.fallback_type || ""))}</span>
                      <span>大模型：{step.metadata?.attempted ? "已调用" : "未调用"}</span>
                      <span>边界：{step.metadata?.applied ? "已采用" : "仍使用规则结果"}</span>
                      {step.metadata?.group_count != null && <span>分组：{String(step.metadata.group_count)}</span>}
                      {step.metadata?.failure_type && <span>失败类型：{getProcessingCodeLabel(String(step.metadata.failure_type))}</span>}
                    </div>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>

        {usage.length > 0 && (
          <section className="rounded-xl border border-slate-200 bg-white p-5">
            <h2 className="mb-3 text-xs font-medium text-slate-600">保存的模型用量（{usage.length}）</h2>
            <div className="space-y-2">
              {usage.map((item, index) => (
                <div key={`${String(item.id || item.component || item.stage || "usage")}-${index}`} className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-slate-500">
                  <span>{item.stage ? getProcessingStageLabel(String(item.stage)) : getModelRoleLabel(String(item.component || ""))}</span>
                  <span>{item.provider ? getProviderLabel(String(item.provider)) : "未记录供应商"}{item.model ? " / " + String(item.model) : ""}</span>
                  <span>调用：{showRecordedNumber(item.calls as number | undefined)}</span>
                  <span>词元数：{showRecordedNumber(item.total_tokens as number | undefined)}</span>
                  {item.cost_cny != null && <span>台账金额：¥{String(item.cost_cny)}</span>}
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
