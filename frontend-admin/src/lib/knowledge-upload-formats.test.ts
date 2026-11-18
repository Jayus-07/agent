import { describe, expect, it } from 'vitest'
import {
  KNOWLEDGE_UPLOAD_ACCEPT,
  KNOWLEDGE_UPLOAD_ALLOWED_EXTENSIONS,
  isSupportedKnowledgeUploadName,
} from './knowledge-upload-formats'

describe('知识库管理端上传格式', () => {
  it('只允许 PDF、DOCX、XLSX、TXT 和 Markdown', () => {
    expect(KNOWLEDGE_UPLOAD_ALLOWED_EXTENSIONS).toEqual([
      '.pdf', '.docx', '.xlsx', '.txt', '.md', '.markdown',
    ])
    expect(KNOWLEDGE_UPLOAD_ACCEPT).toBe('.pdf,.docx,.xlsx,.txt,.md,.markdown')
  })

  it('按扩展名大小写识别支持文件，并拒绝 CSV、PPTX、JSON、XML 和 HTML', () => {
    for (const name of ['manual.PDF', 'guide.docx', 'table.xlsx', 'notes.txt', 'policy.md']) {
      expect(isSupportedKnowledgeUploadName(name)).toBe(true)
    }
    for (const name of ['rows.csv', 'slides.pptx', 'data.json', 'feed.xml', 'page.html']) {
      expect(isSupportedKnowledgeUploadName(name)).toBe(false)
    }
  })
})
