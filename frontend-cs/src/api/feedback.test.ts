import { describe, expect, it } from "vitest";
import { buildFeedbackPayload } from "./feedback";

describe("管理端反馈 payload", () => {
  it("保留负反馈结构化字段", () => {
    expect(buildFeedbackPayload({ session_id: "s1", vote: "negative", correction_text: "修正" })).toMatchObject({ correction_text: "修正" });
  });
});
