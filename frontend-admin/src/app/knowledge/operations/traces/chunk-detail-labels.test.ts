import { describe, expect, it } from "vitest"
import {
  getChunkHeading,
  SIMULATED_QUESTIONS_LABEL,
} from "./chunk-detail-labels"

describe("Trace Chunk 元数据标签", () => {
  it("按结构化范围显示文档级 Parent", () => {
    expect(getChunkHeading({
      granularity: "parent",
      parent_scope: "document",
      document_title: "技术服务合同",
      section_title: "错误的章节回填",
    })).toEqual({
      label: "📄 文档级 Parent",
      title: "技术服务合同",
    })
  })

  it("按结构化范围显示章节级 Parent", () => {
    expect(getChunkHeading({
      granularity: "parent",
      parent_scope: "section",
      section_titles: ["第一条", "第二条"],
    })).toEqual({
      label: "📑 章节级 Parent",
      title: "第一条、第二条",
    })
  })

  it("范围未知的 Parent 不推测为文档或章节", () => {
    expect(getChunkHeading({
      granularity: "parent",
      section_title: "旧数据标题",
    })).toEqual({ label: "Parent", title: "" })
  })

  it("FAQ Leaf 使用对应问题，普通 Leaf 使用章节", () => {
    expect(getChunkHeading({
      granularity: "leaf", doc_type: "faq", section_title: "年假怎么申请？",
    })).toEqual({ label: "📑 对应问题", title: "年假怎么申请？" })
    expect(getChunkHeading({
      granularity: "leaf", doc_type: "policy", section_title: "审批流程",
    })).toEqual({ label: "📑 章节", title: "审批流程" })
  })

  it("问题显示为检索提示", () => {
    expect(SIMULATED_QUESTIONS_LABEL).toBe("检索提示")
  })
})
