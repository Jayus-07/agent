const TEXT_PREVIEW_EXTENSIONS = new Set(['md', 'markdown', 'txt', 'csv', 'json', 'xml', 'html', 'htm'])

export type SourcePreviewKind = 'pdf' | 'text' | null

export function getSourcePreviewKind(filename: string): SourcePreviewKind {
  const normalized = filename.trim().toLowerCase()
  const dot = normalized.lastIndexOf('.')
  if (dot <= 0 || dot === normalized.length - 1) return null
  const extension = normalized.slice(dot + 1)
  if (extension === 'pdf') return 'pdf'
  if (extension && TEXT_PREVIEW_EXTENSIONS.has(extension)) return 'text'
  return null
}
