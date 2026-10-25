import { describe, it, expect } from "vitest";
import {
  connectSteps,
  removeStep,
  graphEdges,
  graphNodes,
  validateGraph,
} from "./graph";
const workflow = {
  id: "flow",
  name: "分支",
  entry_id: "check",
  steps: [
    {
      id: "check",
      type: "condition" as const,
      label: "条件",
      next: "yes",
      otherwise: "no",
    },
    {
      id: "yes",
      type: "template" as const,
      prompt: "通过",
      position: { x: 900, y: 80 },
    },
    { id: "no", type: "template" as const, prompt: "拒绝" },
  ],
};
describe("workflow graph contracts", () => {
  it("renders actual edges, separate conditional ports and saved positions", () => {
    expect(
      graphEdges(workflow).map((e) => [e.source, e.target, e.sourceHandle]),
    ).toEqual([
      ["check", "yes", "next"],
      ["check", "no", "otherwise"],
    ]);
    expect(graphNodes(workflow).find((n) => n.id === "yes")?.position).toEqual({
      x: 900,
      y: 80,
    });
    expect(graphNodes(workflow).find((n) => n.id === "check")?.data.entry).toBe(
      true,
    );
  });
  it("replaces a port connection and rejects cycles without mutating input", () => {
    expect(() =>
      connectSteps(workflow, {
        source: "yes",
        target: "check",
        sourceHandle: "next",
        targetHandle: "in",
      }),
    ).toThrow(/循环/);
    const result = connectSteps(workflow, {
      source: "check",
      target: "no",
      sourceHandle: "next",
      targetHandle: "in",
    });
    expect(result.steps[0].next).toBe("no");
    expect(workflow.steps[0].next).toBe("yes");
  });
  it("removes references to deleted nodes and repairs the entry", () => {
    const result = removeStep(workflow, "check");
    expect(result.entry_id).toBe("yes");
    expect(removeStep(workflow, "yes").steps[0].next).toBeNull();
  });
  it("uses execution transitions rather than array order to mark edges", () => {
    const edges = graphEdges(workflow, [
      { step: "check", status: "completed", next_step: "no" },
    ]);
    expect(edges.find((e) => e.target === "no")?.data?.traversed).toBe(true);
    expect(edges.find((e) => e.target === "yes")?.data?.traversed).toBe(false);
    expect(
      graphNodes(workflow, [{ step: "check", status: "completed" }], true).find(
        (n) => n.id === "yes",
      )?.data.status,
    ).toBe("skipped");
  });
  it("rejects missing entry, duplicate IDs and bad targets", () => {
    expect(() => validateGraph({ ...workflow, entry_id: "missing" })).toThrow();
    expect(() =>
      validateGraph({
        ...workflow,
        steps: [workflow.steps[0], workflow.steps[0]],
      }),
    ).toThrow();
    expect(() =>
      validateGraph({ ...workflow, steps: [workflow.steps[0]] }),
    ).toThrow();
  });
});
