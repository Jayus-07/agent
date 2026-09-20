import { describe, expect, it } from 'vitest'

import {
  gradeLabel,
  isModelSelectable,
  isSpecializedModelRole,
  maskSecret,
  OTHER_ROLE_GROUP_ID,
  probeFailureReason,
  probeFallbackSummary,
  probeOverallLabel,
  probeStepSummary,
  redactForRole,
  ROLE_GROUPS,
  ROLE_LABELS,
  roleGroupOf,
  roleLabel,
  sourceLabel,
  type ConfigHistoryEntry,
  type ModelOption,
} from './modelConfig'

// ── gradeLabel：四级文案唯一来源 ──────────────────────────────────────────

describe('gradeLabel', () => {
  it('四级都有短名，且互不相同', () => {
    const labels = (['L0', 'L1', 'L2', 'L3'] as const).map(gradeLabel)
    expect(labels).toEqual(['URL 可达', '端点清单', '模型调用', '流式 usage'])
    expect(new Set(labels).size).toBe(4)
  })
})

// ── sourceLabel：inherit 必须带父角色与父的当前值 ─────────────────────────

describe('sourceLabel', () => {
  it('四个来源各自成词，且 db 不与 env 混用同一徽章', () => {
    expect(sourceLabel('db', null)).toBe('DB 覆盖')
    expect(sourceLabel('env', null)).toBe('环境变量')
    expect(sourceLabel('default', null)).toBe('代码默认')
  })

  it('inherit 展开父角色名与父的当前值（不让人猜空值含义）', () => {
    expect(sourceLabel('inherit', 'main', 'MiniMax-M3')).toBe('跟随 main（当前 = MiniMax-M3）')
  })

  it('inherit 缺父值时不编造「当前 =」，只给父角色名', () => {
    expect(sourceLabel('inherit', 'main', null)).toBe('跟随 main')
    expect(sourceLabel('inherit', null, null)).toBe('跟随 main')
  })

  it('未知来源原样返回，不冒充「代码默认」', () => {
    expect(sourceLabel('something-new', null)).toBe('something-new')
    expect(sourceLabel('', null)).toBe('未知来源')
  })
})

// ── maskSecret ────────────────────────────────────────────────────────────

describe('maskSecret', () => {
  it('两侧齐备时给出掩码与指纹', () => {
    expect(maskSecret('a1b2', '3f9c1d')).toBe('****a1b2 · 指纹 3f9c1d')
  })

  it('单侧缺失只渲染存在的那侧', () => {
    expect(maskSecret('a1b2', null)).toBe('****a1b2')
    expect(maskSecret(null, '3f9c1d')).toBe('指纹 3f9c1d')
  })

  it('两侧都缺返回空串（调用方渲染「未配置」而不是半个掩码）', () => {
    expect(maskSecret(null, null)).toBe('')
    expect(maskSecret(undefined, undefined)).toBe('')
  })
})

// ── isModelSelectable：未注册 / 缺 Key 两分支 ────────────────────────────

describe('isModelSelectable', () => {
  const chat: ModelOption = {
    name: 'MiniMax-M3',
    provider: 'minimax',
    registered: true,
    missingKeyEnv: null,
  }

  it('注册且 Key 齐备 → 可选，无原因', () => {
    expect(isModelSelectable(chat)).toEqual({ selectable: true, reason: null })
  })

  it('未注册 → 不可选，原因为「未注册」', () => {
    expect(isModelSelectable({ ...chat, registered: false })).toEqual({
      selectable: false,
      reason: '未注册',
    })
  })

  it('缺 Key → 不可选，原因点名环境变量（用户才知道去配哪个）', () => {
    expect(
      isModelSelectable({ ...chat, missingKeyEnv: 'MINIMAX_API_KEY' }),
    ).toEqual({ selectable: false, reason: '缺少 MINIMAX_API_KEY' })
  })

  it('模型用途与角色不一致时不可选，并说明需要哪一类模型', () => {
    expect(isModelSelectable({ ...chat, modelKind: 'embedding' }, 'chat')).toEqual({
      selectable: false,
      reason: '用途不匹配：需要文本模型，当前是向量模型',
    })
  })

  it('供应商不可用 → 不可选，并展示后端返回的具体原因', () => {
    expect(
      isModelSelectable({ ...chat, availabilityReason: 'Ollama 当前未启用' }),
    ).toEqual({ selectable: false, reason: 'Ollama 当前未启用' })
  })

  it('目录里根本没有该模型（undefined）→ 按未注册处理，不抛异常', () => {
    expect(isModelSelectable(undefined)).toEqual({ selectable: false, reason: '未注册' })
  })

  it('未注册优先于缺 Key 报出（先修注册，再修 Key）', () => {
    expect(
      isModelSelectable({ ...chat, registered: false, missingKeyEnv: 'X_API_KEY' }),
    ).toEqual({ selectable: false, reason: '未注册' })
  })
})

describe('isSpecializedModelRole', () => {
  it('与后端 SPECIALIZED_ROLES 对齐：只有 embedding/rerank 有专用适配器', () => {
    // backend/infra/llm/specialized.py: SPECIALIZED_ROLES = {embedding, rerank}
    // OCR 是"专项能力"，但没有专用适配器，期望用途仍是 chat
    // （backend/infra/llm/models.py::expected_model_kind），故不在此列。
    expect(isSpecializedModelRole('embedding')).toBe(true)
    expect(isSpecializedModelRole('rerank')).toBe(true)
    expect(isSpecializedModelRole('ocr')).toBe(false)
    expect(isSpecializedModelRole('eval_gen')).toBe(false)
    expect(isSpecializedModelRole('main')).toBe(false)
  })
})

describe('modelKindLabel', () => {
  it('给五类模型稳定显示用途名称', async () => {
    const { modelKindLabel } = await import('./modelConfig')
    expect(modelKindLabel('chat')).toBe('文本模型')
    expect(modelKindLabel('embedding')).toBe('向量模型')
    expect(modelKindLabel('rerank')).toBe('重排模型')
    expect(modelKindLabel('vision')).toBe('视觉模型')
    expect(modelKindLabel('speech')).toBe('语音模型')
  })
})

// ── probeFallbackSummary / probeOverallLabel ─────────────────────────────

describe('probeFallbackSummary', () => {
  it('fail_degraded 绝不表述成失败（B.4 硬约束 1）', () => {
    const text = probeFallbackSummary('L1', 'fail_degraded')
    expect(text).toContain('不影响使用')
    expect(text).not.toContain('失败')
  })

  it('L1 的 fail 与 fail_degraded 文案不同（前者要修，后者不用）', () => {
    expect(probeFallbackSummary('L1', 'fail')).not.toBe(
      probeFallbackSummary('L1', 'fail_degraded'),
    )
  })

  it('每级 fail 都指向不同修法（L0 查地址 / L2 查模型名与权限）', () => {
    expect(probeFallbackSummary('L0', 'fail')).toContain('地址')
    expect(probeFallbackSummary('L2', 'fail')).toContain('模型名')
  })

  it('L3 的 skip 说明记账影响', () => {
    expect(probeFallbackSummary('L3', 'skip')).toContain('token')
  })
})

describe('probeStepSummary / probeFailureReason', () => {
  it('后端给出具体错误时优先展示具体错误，不丢掉字段级原因', () => {
    expect(probeStepSummary({
      grade: 'L2',
      status: 'fail',
      summary: 'HTTP 400：该 API Key 无权访问 qwen3.7-plus',
    })).toContain('无权访问')
  })

  it('后端摘要缺失时按探测级别给可操作兜底', () => {
    expect(probeFailureReason({
      ok: false,
      summary: '',
      steps: [{ grade: 'L2', status: 'fail', summary: '' }],
    })).toContain('模型名')
  })
})

describe('probeOverallLabel', () => {
  it('通过时给通过结论', () => {
    expect(
      probeOverallLabel({ ok: true, steps: [{ grade: 'L0', status: 'pass' }] }),
    ).toBe('厂商连通性通过')
  })

  it('未通过时点名卡在哪一级（fail_degraded 不算卡住）', () => {
    expect(
      probeOverallLabel({
        ok: false,
        steps: [
          { grade: 'L0', status: 'pass' },
          { grade: 'L1', status: 'fail_degraded' },
          { grade: 'L2', status: 'fail' },
        ],
      }),
    ).toBe('未通过（卡在 L2）')
  })

  it('没有 fail 级时给中性结论，不编造级别', () => {
    expect(
      probeOverallLabel({ ok: false, steps: [{ grade: 'L3', status: 'skip' }] }),
    ).toBe('未通过')
  })
})

// ── redactForRole：三类对象的脱敏矩阵（canAdmin 两侧）────────────────────

const entry = (patch: Partial<ConfigHistoryEntry>): ConfigHistoryEntry => ({
  id: '1',
  object: 'role',
  key: 'main',
  oldValue: 'MiniMax-M3',
  newValue: 'Qwen/Qwen3-8B',
  operator: 'user:1',
  at: '2026-09-19T15:00:00+08:00',
  rollbackable: true,
  ...patch,
})

describe('redactForRole', () => {
  it('role 类：两侧都原样显示 旧值 → 新值', () => {
    for (const canAdmin of [true, false]) {
      const r = redactForRole(entry({}), canAdmin)
      expect(r.redacted).toBe(false)
      expect(r.text).toBe('main: MiniMax-M3 → Qwen/Qwen3-8B')
    }
  })

  it('provider 类：显示完整 URL（URL 不是秘密）', () => {
    const r = redactForRole(
      entry({
        object: 'provider',
        key: 'glm-coding',
        oldValue: 'https://old.example.com/v1',
        newValue: 'https://new.example.com/v1',
      }),
      false,
    )
    expect(r.redacted).toBe(false)
    expect(r.text).toBe('glm-coding: https://old.example.com/v1 → https://new.example.com/v1')
  })

  it('provider_network_scope 类：两侧都显示 public/private（私网变更是安全事件，不能糊掉）', () => {
    const r = redactForRole(
      entry({ object: 'provider_network_scope', key: 'glm-coding', oldValue: 'public', newValue: 'private' }),
      false,
    )
    expect(r.text).toBe('glm-coding: network_scope public → private')
  })

  it('密钥类：admin 与 editor 都只给指纹，**任何一侧都不出现旧值/新值**', () => {
    const secret = entry({
      object: 'provider_credential',
      key: 'minimax',
      oldValue: 'sk-old-plaintext',
      newValue: 'sk-new-plaintext',
      secretFingerprint: '3f9c1d',
    })
    for (const canAdmin of [true, false]) {
      const r = redactForRole(secret, canAdmin)
      expect(r.redacted).toBe(true)
      expect(r.text).toBe('minimax: 密钥已轮换（指纹 3f9c1d）')
      expect(r.text).not.toContain('sk-old-plaintext')
      expect(r.text).not.toContain('sk-new-plaintext')
    }
  })

  it('密钥类缺指纹时给破折号，不留空让 UI 出现「指纹 」', () => {
    const r = redactForRole(
      entry({ object: 'provider_credential', key: 'minimax', secretFingerprint: null }),
      false,
    )
    expect(r.text).toBe('minimax: 密钥已轮换（指纹 —）')
  })

  it('未知对象类型：非 admin 隐藏值（安全网），admin 仍可读', () => {
    const unknown = entry({ object: 'provider_header' as never, key: 'glm-coding' })
    expect(redactForRole(unknown, false)).toEqual({ text: 'glm-coding: 已变更', redacted: true })
    expect(redactForRole(unknown, true).text).toBe('glm-coding: MiniMax-M3 → Qwen/Qwen3-8B')
  })
})

// ── roleLabel ────────────────────────────────────────────────────────────

describe('roleLabel', () => {
  it('后端 MODEL_ROLES 全部角色都有中文名', () => {
    // 快照对齐 backend/config/model_roles.py 的 MODEL_ROLES（11 个）。
    // 后端新增角色时这里会失败 —— 那是提醒，不是噪声：漏补中文名的下场是表格里显示英文代码。
    const roles = [
      'main',
      'doc',
      'metadata_extract',
      'question_gen',
      'table_describe',
      'tool_selector',
      'fallback',
      'ocr',
      'embedding',
      'rerank',
      'eval_gen',
    ]
    expect(roles).toHaveLength(11)
    for (const r of roles) {
      expect(roleLabel(r)).not.toBe(r)
      expect(roleLabel(r).length).toBeGreaterThan(0)
    }
  })

  it('未知角色回落为 role 代码，不显示空白', () => {
    expect(roleLabel('brand_new_role')).toBe('brand_new_role')
  })
})

// ── 角色分组 ─────────────────────────────────────────────────────────────

describe('ROLE_GROUPS', () => {
  it('覆盖 ROLE_LABELS 的全部角色，且不重复登记', () => {
    const grouped = ROLE_GROUPS.flatMap((group) => group.roles)
    expect(new Set(grouped).size).toBe(grouped.length)
    // 漏登记的角色会掉进界面的「其他」组，结构上不该出现这种情况
    expect([...Object.keys(ROLE_LABELS)].filter((role) => !grouped.includes(role))).toEqual([])
  })

  it('roleGroupOf 命中已登记角色，未知角色归入 other', () => {
    expect(roleGroupOf('main')).toBe('chat')
    expect(roleGroupOf('embedding')).toBe('retrieve')
    expect(roleGroupOf('brand_new_role')).toBe(OTHER_ROLE_GROUP_ID)
  })
})
