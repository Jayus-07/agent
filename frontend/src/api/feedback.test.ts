import { describe, expect, it } from "vitest";
import { buildFeedbackPayload } from "./feedback";

describe("结构化负反馈", () => {
  it("同时提交原因、纠错内容、期望答案和 trace 归属", () => {
    expect(buildFeedbackPayload({
      session_id: "s1", vote: "negative", reason: "不准确",
      correction_text: "正确答案是……", expected_answer: "应回答……", trace_id: "t1",
    })).toMatchObject({
      reason: "不准确", correction_text: "正确答案是……", expected_answer: "应回答……", trace_id: "t1",
    });
  });
});
