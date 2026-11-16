import { describe, expect, it } from 'vitest'
import { chooseAssistantErrorDisplay, presentAssistantAnswer } from './assistantAnswerPresentation'

describe('chooseAssistantErrorDisplay', () => {
  it('shows an SSE error only in the dedicated alert when the answer repeats it', () => {
    expect(chooseAssistantErrorDisplay('## SQL 查询暂时失败', 'SQL 查询暂时失败')).toBe('alert')
  })

  it('shows a transport failure only in the dedicated alert when no partial answer exists', () => {
    expect(chooseAssistantErrorDisplay('## 请求失败\n\n网络连接中断', '网络连接中断')).toBe('alert')
  })

  it('keeps a partial answer with its inline interruption warning without duplicating the alert', () => {
    expect(chooseAssistantErrorDisplay(
      '已查到部分内容。\n\n---\n\n⚠️ **请求中断**: 网络连接中断',
      '网络连接中断',
    )).toBe('answer')
  })

  it('keeps unrelated answer content and shows a distinct error alert', () => {
    expect(chooseAssistantErrorDisplay('已完成部分分析。', '数据库连接失败')).toBe('both')
  })
})

describe('presentAssistantAnswer', () => {
  it('removes internal document markers from visible text', () => {
    expect(presentAssistantAnswer('规则说明 <!--doc:71a1ad2d5f-->')).toBe('规则说明')
  })

  it('uses structured source cards instead of a duplicate reference appendix', () => {
    const answer = '结论见上文。<!--doc:doc-1-->\n\n---\n\n### 参考文献\n\n1. **退款规则.md**'

    expect(presentAssistantAnswer(answer, true)).toBe('结论见上文。')
  })

  it('keeps the reference appendix when there are no structured sources', () => {
    const answer = '结论见上文。\n\n### 参考文献\n\n1. **退款规则.md**'

    expect(presentAssistantAnswer(answer)).toBe(answer)
  })

  it('replaces only a Markdown table that matches structured SQL rows', () => {
    const sqlResult = {
      title: '商品销量',
      columns: ['商品', '销量'],
      rows: [['A|B', 20]],
      total_rows: 1,
      omitted_columns: 0,
    }
    const answer = [
      '查询完成。',
      '| 商品 | 销量 |',
      '|:---:|:---:|',
      '| A\\|B | 20 |',
      '',
      '| 部门 | 人数 |',
      '|:---:|:---:|',
      '| 研发 | 10 |',
    ].join('\n')

    const presented = presentAssistantAnswer(answer, false, [sqlResult])
    expect(presented).not.toContain('| 商品 | 销量 |')
    expect(presented).toContain('| 部门 | 人数 |')
    expect(presented).toContain('查询完成。')
  })

  it('keeps the natural-language conclusion and removes duplicate SQL row count', () => {
    const answer = [
      '## 查询结果',
      '',
      '### 数据库查询',
      '',
      '本月销售额为 12,345 元。',
      '查询成功，共 1 行。',
      '',
      '| 月份 | 销售额 |',
      '|:---:|:---:|',
      '| 2026-11 | 12345 |',
    ].join('\n')
    const sqlResult = {
      title: '本月销售额',
      columns: ['月份', '销售额'],
      rows: [['2026-11', 12345]],
      total_rows: 1,
      omitted_columns: 0,
    }

    const presented = presentAssistantAnswer(answer, false, [sqlResult])

    expect(presented).toContain('本月销售额为 12,345 元。')
    expect(presented).not.toContain('查询成功，共 1 行。')
    expect(presented).not.toContain('| 月份 | 销售额 |')
  })

  it('removes duplicated SQL count and truncation note when the card shows the same rows', () => {
    const rows = Array.from({ length: 20 }, (_, index) => [`2026-${index + 1}`, index + 1])
    const sqlResult = {
      title: '月度销售额',
      columns: ['月份', '销售额'],
      rows,
      total_rows: 100,
      omitted_columns: 0,
    }
    const answer = [
      '月度销售额整体呈上升趋势。',
      '',
      '查询成功，共 100 行。',
      '',
      '| 月份 | 销售额 |',
      '|:---:|:---:|',
      ...rows.map(([month, amount]) => `| ${month} | ${amount} |`),
      '',
      '*(共 100 行，仅显示前 20 行)*',
    ].join('\n')

    const presented = presentAssistantAnswer(answer, false, [sqlResult])

    expect(presented).toContain('月度销售额整体呈上升趋势。')
    expect(presented).not.toContain('查询成功，共 100 行。')
    expect(presented).not.toContain('| 月份 | 销售额 |')
    expect(presented).not.toContain('仅显示前 20 行')
  })

  it('removes a generic count that repeats only the card preview count', () => {
    const sqlResult = {
      title: '订单明细',
      columns: ['订单号'],
      rows: [['A-1'], ['A-2']],
      total_rows: 100,
      omitted_columns: 0,
    }

    expect(presentAssistantAnswer('查询成功，共 2 行。', false, [sqlResult]))
      .not.toContain('查询成功，共 2 行。')
  })

  it('keeps a truncation note when the card does not contain the stated preview row count', () => {
    const sqlResult = {
      title: '月度销售额',
      columns: ['月份', '销售额'],
      rows: [['2026-01', 10]],
      total_rows: 100,
      omitted_columns: 0,
      payload_truncated: true,
    }
    const note = '*(共 100 行，仅显示前 20 行)*'

    expect(presentAssistantAnswer(note, false, [sqlResult])).toContain(note)
  })

  it('removes the generic empty-row sentence when the result card states no matches', () => {
    const answer = '## 查询结果\n\n### 数据库查询\n\n查询成功，但没有符合条件的数据（0 行）。'
    const sqlResult = {
      title: '本月退款记录', columns: ['订单号'], rows: [],
      total_rows: 0, omitted_columns: 0,
    }

    expect(presentAssistantAnswer(answer, false, [sqlResult]))
      .not.toContain('查询成功，但没有符合条件的数据（0 行）。')
  })
})
