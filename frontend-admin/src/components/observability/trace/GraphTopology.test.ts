import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { Span } from "@/types/trace";
import GraphTopology, { findGraphNodeSpan, hasGraphNodeExecution } from "./GraphTopology";

let mounted: { root: Root; container: HTMLDivElement } | null = null;

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  mounted?.root.unmount();
  mounted?.container.remove();
  mounted = null;
});

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

  it("clicks an aliased graph node using the matched Span ID", async () => {
    const onNodeClick = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    mounted = { root, container };

    await act(async () => {
      root.render(createElement(GraphTopology, {
        nodes: [{ id: "skill-lookup", label: "Skill lookup" }],
        edges: [],
        spans: [span("skill_executor:lookup", "success")],
        loopCount: 0,
        maxLoops: 3,
        degradationTriggered: false,
        onNodeClick,
      }));
    });

    const nodeButton = container.querySelector<HTMLButtonElement>('button[aria-label^="执行节点"]');
    expect(nodeButton).toBeTruthy();
    await act(async () => nodeButton?.click());
    expect(onNodeClick).toHaveBeenCalledWith("skill_executor:lookup");
  });
});
