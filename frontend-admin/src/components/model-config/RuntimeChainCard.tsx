'use client'

import { useMemo } from 'react'
import { ArrowRight, GitBranch } from 'lucide-react'
import type { ModelCatalogEntry } from '@/api/modelConfig'
import type { RoleBinding } from '@/types/modelConfig'
import { roleLabel } from '@/types/modelConfig'

interface Props {
  roles: RoleBinding[]
  catalog: ModelCatalogEntry[]
}

/**
 * RuntimeChainCard — 当前运行时模型链路（治理改造 2026-09-22，用户规格 §16）。
 *
 * 按问答 / 入库两条链路把各角色的**当前生效模型**串成一条可视链：
 * 数据全部来自 `GET /sys/model-roles` 的真实配置（不引入第二个事实源），
 * 继承 main 的角色显示为「跟随 main」。零动画，只求清楚、真实、可维护。
 */

type RoleMap = Map<string, RoleBinding>

function modelName(map: RoleMap, role: string): string {
  const row = map.get(role)
  if (!row?.effectiveModel) return '未配置'
  return row.effectiveModel
}

function Step({ label, model, muted }: { label: string; model: string; muted?: boolean }) {
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <div
        className={`rounded-lg border px-2.5 py-1.5 ${
          muted
            ? 'border-slate-200 bg-slate-50'
            : 'border-black/5 bg-white shadow-sm'
        }`}
      >
        <div className="text-[10px] leading-none text-text-muted">{label}</div>
        <div className="mt-1 max-w-[220px] truncate font-mono text-[11px] leading-none text-text-primary" title={model}>
          {model}
        </div>
      </div>
    </div>
  )
}

function Chain({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <div className="border-b border-slate-100 px-4 py-3 last:border-0">
      <div className="flex items-baseline gap-2">
        <span className="text-[11px] font-medium text-text-secondary">{title}</span>
        <span className="text-[10px] text-text-muted">{hint}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1">{children}</div>
    </div>
  )
}

function Arrow() {
  return <ArrowRight size={12} className="shrink-0 text-slate-300" />
}

export default function RuntimeChainCard({ roles, catalog: _catalog }: Props) {
  void _catalog // 目录仅用于未来展示模型能力，当前链路只消费角色绑定
  const byRole = useMemo(() => new Map(roles.map((item) => [item.role, item])), [roles])
  const mainModel = modelName(byRole, 'main')

  return (
    <section className="overflow-hidden rounded-xl border border-black/5 bg-slate-50/60 shadow-card" data-testid="runtime-chain-card">
      <div className="flex items-center gap-2 border-b border-slate-100 bg-white px-4 py-3">
        <GitBranch size={14} className="text-accent" />
        <h2 className="text-xs font-medium text-text-primary">当前运行时模型链路</h2>
        <span className="text-[10px] text-text-muted">按角色绑定动态生成 · 每 30s 随配置刷新</span>
      </div>

      {/* 问答链路：QueryRouter → ToolSelector → RAG → Main → Fallback */}
      <Chain title="问答链路" hint="用户问题 → 理解路由 → 生成">
        <Step label="用户问题" model="—" muted />
        <Arrow />
        <div className="rounded-lg border border-dashed border-slate-300 px-2.5 py-1.5">
          <div className="text-[10px] leading-none text-text-muted">QueryRouter</div>
          <div className="mt-1 font-mono text-[11px] leading-none text-text-secondary">规则 → 向量 → LLM 兜底</div>
        </div>
        <Arrow />
        <Step label="ToolSelector" model={modelName(byRole, 'tool_selector')} />
        <Arrow />
        <div className="rounded-lg border border-black/5 bg-white px-2.5 py-1.5 shadow-sm">
          <div className="text-[10px] leading-none text-text-muted">RAG</div>
          <div className="mt-1 font-mono text-[11px] leading-none text-text-primary" title={modelName(byRole, 'embedding')}>
            向量: {modelName(byRole, 'embedding')}
          </div>
          <div className="mt-0.5 font-mono text-[11px] leading-none text-text-primary" title={modelName(byRole, 'rerank')}>
            重排: {modelName(byRole, 'rerank')}
          </div>
        </div>
        <Arrow />
        <Step label="Main" model={mainModel} />
        <Arrow />
        <Step label="Fallback" model={modelName(byRole, 'fallback')} muted />
      </Chain>

      {/* 入库链路：Upload → Doc → Metadata → QuestionGen → Table → OCR → Embedding → Index */}
      <Chain title="入库链路" hint="文档上传 → 解析抽取 → 建索引（各阶段可空，空 = 跟随 main）">
        <Step label="Upload" model="—" muted />
        <Arrow />
        <Step label="Doc 抽取" model={modelName(byRole, 'doc')} />
        <Arrow />
        <Step label="元数据抽取" model={modelName(byRole, 'metadata_extract')} />
        <Arrow />
        <Step label="问题生成" model={modelName(byRole, 'question_gen')} />
        <Arrow />
        <Step label="表格描述" model={modelName(byRole, 'table_describe')} />
        <Arrow />
        <Step label="OCR" model={modelName(byRole, 'ocr')} />
        <Arrow />
        <Step label="向量化" model={modelName(byRole, 'embedding')} />
        <Arrow />
        <Step label="Index" model="pgvector" muted />
      </Chain>

      <div className="border-t border-slate-100 bg-white px-4 py-2 text-[10px] text-text-muted">
        「跟随 main」的角色未配置独立模型，运行时使用 Main 的当前绑定（{mainModel}）。
        模型可在下方角色绑定表修改；配置来源与审计见对应行。
      </div>
    </section>
  )
}
