import { describe, expect, it } from "vitest";
import type { Span } from "@/types/trace";
import { findGraphNodeSpan, hasGraphNodeExecution } from "./GraphTopology";

function span(id: string, status: string): Span {
  return { id, status } as Span;
}

describe("Trace graph execution evidence", () => {
  it("only associates graph nodes backed by an actual Span", () => {
    const spans = [span("router", "success"), span("skill_executor:lookup", "error")];

    expect(hasGraphNodeExecution("router", spans)).toBe(true);
    expect(findGraphNodeSpan("skill-lookup", spans)?.status).toBe("error");
    expect(hasGraphNodeExecution("skill-not-run", spans)).toBe(false);
  });

  it("matches an actual supervisor round to the supervisor node", () => {
    const spans = [span("supervisor-round-2", "partial")];

    expect(findGraphNodeSpan("supervisor", spans)?.status).toBe("partial");
  });
});
