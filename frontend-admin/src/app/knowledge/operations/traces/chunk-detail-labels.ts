export function getChunkSectionLabel(docType?: string): string {
  return docType === "faq" ? "对应问题" : "章节"
}

export const SIMULATED_QUESTIONS_LABEL = "检索提示"
