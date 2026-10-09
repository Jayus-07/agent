/** 附加请求体（087 迁移）的纯函数契约：预设映射、往返一致性与非法输入拦截。
 *
 * 背景：管理端需要能关闭思考型模型的推理（实测 reasoning/completion=0.73，
 * 73% 生成 token 花在思考上）。后端已支持供应商级 extra_body，本组测试锁
 * 前端的预设解析 —— 非法 JSON 必须就地报错，不能等到保存才炸。
 */
import { describe, expect, it } from 'vitest'
import { EXTRA_BODY_PRESETS, extraBodyToDraft, resolveExtraBody } from './draft'

describe('extraBodyToDraft — 库中值反推预设', () => {
  it('空对象与 undefined 都落到 none', () => {
    expect(extraBodyToDraft({})).toEqual({ preset: 'none', text: '' })
    expect(extraBodyToDraft(undefined)).toEqual({ preset: 'none', text: '' })
  })

  it('识别关闭思考预设', () => {
    expect(extraBodyToDraft({ thinking: { type: 'disabled' } }).preset).toBe('thinking-off')
  })

  it('识别低推理强度预设', () => {
    expect(extraBodyToDraft({ reasoning_effort: 'low' }).preset).toBe('low-effort')
  })

  it('未知结构回落 custom 并保留原文', () => {
    const out = extraBodyToDraft({ foo: 1 })
    expect(out.preset).toBe('custom')
    expect(JSON.parse(out.text)).toEqual({ foo: 1 })
  })
})

describe('resolveExtraBody — 预设与文本解析', () => {
  it('none 产出空对象', () => {
    expect(resolveExtraBody('none', '')).toEqual({ value: {} })
  })

  it('预设产出正确字段', () => {
    expect(resolveExtraBody('thinking-off', '')).toEqual({
      value: { thinking: { type: 'disabled' } },
    })
    expect(resolveExtraBody('low-effort', '')).toEqual({
      value: { reasoning_effort: 'low' },
    })
  })

  it('custom 解析合法 JSON 对象', () => {
    expect(resolveExtraBody('custom', '{"enable_thinking":false}')).toEqual({
      value: { enable_thinking: false },
    })
  })

  it('custom 空文本等于不附加', () => {
    expect(resolveExtraBody('custom', '   ')).toEqual({ value: {} })
  })

  it.each([
    ['非法 JSON', '{not json'],
    ['数组', '[1,2]'],
    ['null', 'null'],
    ['字符串', '"abc"'],
  ])('custom 拒绝 %s', (_name, text) => {
    const out = resolveExtraBody('custom', text)
    expect('error' in out).toBe(true)
  })
})

describe('预设往返一致性', () => {
  it('每个可构建的预设都能被反推识别', () => {
    for (const preset of EXTRA_BODY_PRESETS.filter((item) => item.build)) {
      const built = preset.build!()
      expect(extraBodyToDraft(built).preset).toBe(preset.value)
    }
  })

  it('预设表包含关闭思考与低推理强度两类', () => {
    const values = EXTRA_BODY_PRESETS.map((item) => item.value)
    expect(values).toContain('thinking-off')
    expect(values).toContain('low-effort')
    expect(values).toContain('custom')
  })
})
