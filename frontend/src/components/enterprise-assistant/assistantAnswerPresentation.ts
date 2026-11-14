const REFERENCE_SECTION_MARKERS = [
  '\n\n---\n\n### 参考文献',
  '\n\n---\n\n### 参考来源',
  '\n\n### 参考文献',
  '\n\n### 参考来源',
]

/**
 * 清理面向用户的回答文本，保留结构化来源卡片作为唯一来源展示。
 */
export function presentAssistantAnswer(
  content: string,
  hasStructuredSources = false,
): string {
  const cleaned = content
    .replace(/<!--\s*doc:[^>]*-->/gi, '')
    .replace(/[ \t]+$/gm, '')
  if (!hasStructuredSources) return cleaned

  for (const marker of REFERENCE_SECTION_MARKERS) {
    const index = cleaned.indexOf(marker)
    if (index !== -1) return cleaned.slice(0, index)
  }
  return cleaned
}
