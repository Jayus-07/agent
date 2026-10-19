export interface ChunkHeadingMetadata {
  granularity?: string
  parent_scope?: string
  document_title?: string
  section_title?: string
  section_titles?: string[]
  doc_type?: string
}

export interface ChunkHeadingDisplay {
  label: string
  title: string
}

export function getChunkHeading(
  chunk: ChunkHeadingMetadata,
): ChunkHeadingDisplay | null {
  const sectionTitle = chunk.section_title?.trim() ?? ""

  if (chunk.granularity === "parent") {
    if (chunk.parent_scope === "document") {
      return {
        label: "📄 文档级 Parent",
        title: chunk.document_title?.trim() ?? "",
      }
    }
    if (chunk.parent_scope === "section") {
      const sectionTitles = (chunk.section_titles ?? [])
        .map((title) => title.trim())
        .filter(Boolean)
      return {
        label: "📑 章节级 Parent",
        title: sectionTitle || sectionTitles.join("、"),
      }
    }
    return { label: "Parent", title: "" }
  }

  if (chunk.granularity !== "leaf") return null
  if (chunk.doc_type === "faq") {
    return { label: "📑 对应问题", title: sectionTitle }
  }
  return sectionTitle ? { label: "📑 章节", title: sectionTitle } : null
}

export const SIMULATED_QUESTIONS_LABEL = "检索提示"
