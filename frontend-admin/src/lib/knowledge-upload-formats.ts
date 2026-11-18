export const KNOWLEDGE_UPLOAD_ALLOWED_EXTENSIONS = [
  '.pdf', '.docx', '.xlsx', '.txt', '.md', '.markdown',
] as const

export const KNOWLEDGE_UPLOAD_ACCEPT = KNOWLEDGE_UPLOAD_ALLOWED_EXTENSIONS.join(',')
export const KNOWLEDGE_UPLOAD_FORMAT_LABEL =
  'PDF、Word（.docx）、Excel（.xlsx）、TXT（.txt）和 Markdown（.md/.markdown）'

export function isSupportedKnowledgeUploadName(name: string): boolean {
  const normalized = name.trim().toLowerCase()
  return KNOWLEDGE_UPLOAD_ALLOWED_EXTENSIONS.some(extension => normalized.endsWith(extension))
}
