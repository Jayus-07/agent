import type { SQLResultSet } from '@/lib/types'

const REFERENCE_SECTION_MARKERS = [
  '\n\n---\n\n### 参考文献',
  '\n\n---\n\n### 参考来源',
  '\n\n### 参考文献',
  '\n\n### 参考来源',
]

export type AssistantErrorDisplay = 'answer' | 'alert' | 'both'

/** 避免同一错误在助手正文和错误提示中重复，同时保留中断前已生成的回答。 */
export function chooseAssistantErrorDisplay(content: string, errorMessage: string): AssistantErrorDisplay {
  const answer = content.trim()
  const message = errorMessage.trim()
  if (!message) return 'answer'

  if (answer === `## ${message}` || answer === `## 请求失败\n\n${message}`) {
    return 'alert'
  }
  if (answer.endsWith(`⚠️ **请求中断**: ${message}`)) return 'answer'
  return 'both'
}

/**
 * 清理面向用户的回答文本，保留结构化来源卡片作为唯一来源展示。
 */
export function presentAssistantAnswer(
  content: string,
  hasStructuredSources = false,
  sqlResults: SQLResultSet[] = [],
): string {
  const cleaned = content
    .replace(/<!--\s*doc:[^>]*-->/gi, '')
    .replace(/[ \t]+$/gm, '')
  let presented = cleaned
  if (hasStructuredSources) {
    for (const marker of REFERENCE_SECTION_MARKERS) {
      const index = presented.indexOf(marker)
      if (index !== -1) {
        presented = presented.slice(0, index)
        break
      }
    }
  }
  return removeRenderedSQLTables(presented, sqlResults)
}

function markdownCells(line: string): string[] {
  const cells: string[] = []
  let cell = ''
  let escaped = false
  for (const character of line.trim()) {
    if (character === '|') {
      if (escaped) {
        cell += character
        escaped = false
      } else {
        cells.push(cell)
        cell = ''
      }
    } else {
      cell += character
      if (character === '\\') escaped = !escaped
      else escaped = false
    }
  }
  cells.push(cell)
  if (line.trim().startsWith('|')) cells.shift()
  if (line.trim().endsWith('|')) cells.pop()
  return cells.map((value) => value.trim())
}

function normalizeMarkdownCell(value: string): string {
  return value
    .replace(/\\([\\|*_`~\[\]])/g, '$1')
    .replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>')
    .replaceAll('&quot;', '"')
    .replaceAll('&#x27;', "'")
    .replaceAll('&amp;', '&')
}

function expectedCell(value: string | number | boolean | null): string {
  return value == null ? '—' : String(value).replace(/[\r\n]+/g, ' / ')
}

function isSameSQLTable(
  header: string[],
  rows: string[][],
  result: SQLResultSet,
): boolean {
  const expectedRows = [result.columns, ...result.rows.map((row) =>
    result.columns.map((_, index) => expectedCell(row[index] ?? null)),
  )]
  if (header.length !== result.columns.length || rows.length + 1 !== expectedRows.length) return false
  return [header, ...rows].every((row, rowIndex) => row.length === expectedRows[rowIndex].length
    && row.every((cell, columnIndex) => normalizeMarkdownCell(cell) === expectedRows[rowIndex][columnIndex]))
}

function isDuplicateSQLSummary(line: string, sqlResults: SQLResultSet[]): boolean {
  const normalized = line.trim()
  const countMatch = normalized.match(/^查询成功，共\s*(\d+)\s*行。$/)
  if (countMatch) {
    const rowCount = Number(countMatch[1])
    return sqlResults.some((result) => result.total_rows === rowCount
      || (result.total_rows > result.rows.length && result.rows.length === rowCount))
  }
  const truncationMatch = normalized.match(/^\*\(共\s*(\d+)\s*行，仅显示前\s*(\d+)\s*行\)\*$/)
  if (truncationMatch) {
    const totalRows = Number(truncationMatch[1])
    const displayedRows = Number(truncationMatch[2])
    return sqlResults.some((result) => result.total_rows === totalRows
      && result.rows.length === displayedRows
      && result.total_rows > result.rows.length)
  }
  return normalized === '查询成功，但没有符合条件的数据（0 行）。'
    && sqlResults.some((result) => result.total_rows === 0)
}

/** 仅折叠结构化 SQL 卡已呈现的表格和固定行数提示；其他报告内容保留。 */
export function removeRenderedSQLTables(content: string, sqlResults: SQLResultSet[]): string {
  if (!sqlResults.length) return content
  const lines = content.split('\n').filter((line) => !isDuplicateSQLSummary(line, sqlResults))
  if (!content.includes('|')) {
    return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim()
  }
  const isSeparator = (line: string) => {
    const cells = markdownCells(line)
    return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell))
  }

  for (let index = 0; index < lines.length - 1; index += 1) {
    if (!isSeparator(lines[index + 1])) continue
    const header = markdownCells(lines[index])
    const tableRows: string[][] = []
    let end = index + 2
    while (end < lines.length && /^\s*\|/.test(lines[end])) {
      tableRows.push(markdownCells(lines[end]))
      end += 1
    }
    if (!sqlResults.some((result) => isSameSQLTable(header, tableRows, result))) continue
    for (let lineIndex = index; lineIndex < end; lineIndex += 1) lines[lineIndex] = ''
    index = end - 1
  }

  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim()
}
