import { describe, expect, it } from "vitest";
import { canPromoteCandidate } from "./feedbackCandidates";

describe("反馈候选状态机", () => {
  it("只有 approved 可以 promotion", () => {
    expect(canPromoteCandidate("pending")).toBe(false);
    expect(canPromoteCandidate("rejected")).toBe(false);
    expect(canPromoteCandidate("approved")).toBe(true);
    expect(canPromoteCandidate("promoted")).toBe(false);
  });
});
