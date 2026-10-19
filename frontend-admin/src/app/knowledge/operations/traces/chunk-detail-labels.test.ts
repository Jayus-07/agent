import { describe, expect, it } from "vitest"
import {
  getChunkSectionLabel,
  SIMULATED_QUESTIONS_LABEL,
} from "./chunk-detail-labels"

describe("Trace Chunk 元数据标签", () => {
  it("FAQ 标题显示为对应问题，其他文档仍显示章节", () => {
    expect(getChunkSectionLabel("faq")).toBe("对应问题")
    expect(getChunkSectionLabel("policy")).toBe("章节")
    expect(getChunkSectionLabel()).toBe("章节")
  })

  it("问题显示为检索提示", () => {
    expect(SIMULATED_QUESTIONS_LABEL).toBe("检索提示")
  })
})
