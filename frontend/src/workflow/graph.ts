import type { Connection, Edge, Node } from "@xyflow/react";
export type StepType = "prompt" | "template" | "condition" | "tool";
export type Step = {
  id: string;
  type: StepType;
  label?: string;
  position?: { x: number; y: number } | null;
  prompt?: string;
  model_id?: string;
  skill_ids?: string[];
  mcp_ids?: string[];
  server_id?: string;
  tool_name?: string;
  arguments?: Record<string, unknown>;
  contains?: string;
  next?: string | null;
  otherwise?: string | null;
  retries?: number;
};
export type Workflow = {
  id: string;
  name: string;
  description?: string;
  entry_id?: string | null;
  steps: Step[];
};
export type Trace = {
  step: string;
  status: string;
  next_step?: string | null;
  output?: string;
  attempts?: number;
  matched?: boolean;
  events?: Record<string, unknown>[];
};
export type Run = {
  id?: string;
  name?: string;
  status: string;
  trace: Trace[];
  output?: string;
  error?: string;
  workflow_snapshot?: Workflow;
  current_step?: string | null;
};
export const typeNames: Record<StepType, string> = {
  prompt: "模型 / Agent",
  template: "文本模板",
  condition: "条件分支",
  tool: "MCP 工具",
};
export type WorkflowNode = Node<
  { step: Step; entry: boolean; status: string; summary: string },
  "workflow"
>;

export function validateGraph(workflow: Workflow) {
  if (
    !workflow ||
    !Array.isArray(workflow.steps) ||
    !workflow.steps.length ||
    workflow.steps.length > 40
  )
    throw new Error("流程需要 1–40 个节点");
  for (const s of workflow.steps) {
    if (
      !s ||
      typeof s !== "object" ||
      typeof s.id !== "string" ||
      !Object.hasOwn(typeNames, s.type)
    )
      throw new Error("节点 ID 或类型无效");
    for (const key of [
      "label",
      "prompt",
      "model_id",
      "server_id",
      "tool_name",
      "contains",
    ] as const)
      if (s[key] !== undefined && typeof s[key] !== "string")
        throw new Error("节点文本配置无效");
    for (const key of ["skill_ids", "mcp_ids"] as const)
      if (
        s[key] !== undefined &&
        (!Array.isArray(s[key]) || s[key]!.some((id) => typeof id !== "string"))
      )
        throw new Error("技能或 MCP 配置无效");
    if (
      s.arguments !== undefined &&
      (!s.arguments ||
        typeof s.arguments !== "object" ||
        Array.isArray(s.arguments))
    )
      throw new Error("工具参数必须是 JSON 对象");
    if (
      s.retries !== undefined &&
      (!Number.isInteger(s.retries) || s.retries < 0 || s.retries > 3)
    )
      throw new Error("重试次数必须为 0–3");
    for (const key of ["next", "otherwise"] as const)
      if (s[key] !== undefined && s[key] !== null && typeof s[key] !== "string")
        throw new Error("连线目标无效");
    if (s.type !== "condition" && s.otherwise)
      throw new Error("只有条件节点可以配置不匹配分支");
  }
  const nodes = new Map(workflow.steps.map((s) => [s.id, s]));
  if (nodes.size !== workflow.steps.length) throw new Error("节点 ID 不可重复");
  if (!nodes.has(workflow.entry_id ?? workflow.steps[0].id))
    throw new Error("开始节点不存在");
  const active = new Set<string>(),
    visited = new Set<string>();
  function visit(id: string) {
    if (active.has(id)) throw new Error("连线会形成循环，不能保存");
    if (visited.has(id)) return;
    const step = nodes.get(id);
    if (!step) throw new Error("连线指向不存在的节点：" + id);
    if (!/^[a-zA-Z0-9_-]{1,64}$/.test(id) || !(step.type in typeNames))
      throw new Error("节点 ID 或类型无效");
    if (
      step.position &&
      (!Number.isFinite(step.position.x) || !Number.isFinite(step.position.y))
    )
      throw new Error("节点坐标无效");
    active.add(id);
    for (const target of [step.next, step.otherwise]) if (target) visit(target);
    active.delete(id);
    visited.add(id);
  }
  for (const id of nodes.keys()) visit(id);
}

export function connectSteps(
  workflow: Workflow,
  connection: Connection,
): Workflow {
  if (!connection.source || !connection.target)
    throw new Error("请选择两个节点的连接端口");
  const source = workflow.steps.find((s) => s.id === connection.source);
  const handle = connection.sourceHandle || "next";
  if (
    !source ||
    !["next", "otherwise"].includes(handle) ||
    (handle === "otherwise" && source.type !== "condition")
  )
    throw new Error("无效的输出端口");
  const result = {
    ...workflow,
    steps: workflow.steps.map((s) =>
      s.id === connection.source ? { ...s, [handle]: connection.target } : s,
    ),
  };
  validateGraph(result);
  return result;
}

export function removeStep(workflow: Workflow, id: string): Workflow {
  const steps = workflow.steps
    .filter((s) => s.id !== id)
    .map((s) => ({
      ...s,
      next: s.next === id ? null : s.next,
      otherwise: s.otherwise === id ? null : s.otherwise,
    }));
  return {
    ...workflow,
    steps,
    entry_id:
      (workflow.entry_id || workflow.steps[0]?.id) === id
        ? steps[0]?.id
        : workflow.entry_id,
  };
}

export function graphEdges(workflow: Workflow, trace: Trace[] = []): Edge[] {
  return workflow.steps.flatMap((s) =>
    ["next", "otherwise"].flatMap((port) => {
      const target = s[port as "next" | "otherwise"];
      if (!target) return [];
      const traversed = trace.some(
        (t) =>
          t.step === s.id &&
          t.status === "completed" &&
          t.next_step === target &&
          (s.type !== "condition" ||
            t.matched === undefined ||
            port === (t.matched === false ? "otherwise" : "next")),
      );
      return [
        {
          id: `${s.id}-${port}`,
          source: s.id,
          target,
          sourceHandle: port,
          targetHandle: "in",
          label:
            s.type === "condition"
              ? port === "next"
                ? "匹配"
                : "不匹配"
              : "下一步",
          type: "smoothstep",
          markerEnd: { type: "arrowclosed" as any, width: 18, height: 18 },
          animated: traversed,
          style: {
            stroke: traversed
              ? "#36a77d"
              : port === "otherwise"
                ? "#cf9b62"
                : "#8795be",
            strokeWidth: traversed ? 3 : 2,
          },
          data: { traversed },
          labelStyle: { fontSize: 11, fill: "#687590" },
        },
      ];
    }),
  );
}

export function graphNodes(
  workflow: Workflow,
  trace: Trace[] = [],
  finished = false,
): WorkflowNode[] {
  const entry = workflow.entry_id || workflow.steps[0]?.id;
  const levels = new Map<string, number>();
  const queue = entry ? [entry] : [];
  levels.set(entry, 0);
  for (let index = 0; index < queue.length; index++) {
    const id = queue[index],
      step = workflow.steps.find((s) => s.id === id);
    for (const next of [step?.next, step?.otherwise])
      if (next && !levels.has(next)) {
        levels.set(next, (levels.get(id) || 0) + 1);
        queue.push(next);
      }
  }
  const rows = new Map<number, number>();
  return workflow.steps.map((step, index) => {
    const level = levels.get(step.id) ?? 0,
      row = rows.get(level) || 0;
    rows.set(level, row + 1);
    const status =
      trace.find((t) => t.step === step.id)?.status ||
      (finished ? "skipped" : "idle");
    const summary =
      step.type === "prompt"
        ? step.model_id || "使用运行时模型"
        : step.type === "tool"
          ? step.tool_name || "选择 MCP 工具"
          : step.type === "condition"
            ? `包含：${step.contains || "（空）"}`
            : step.prompt || "输入模板内容";
    return {
      id: step.id,
      type: "workflow",
      position: step.position || { x: level * 310, y: row * 180 + 40 },
      data: {
        step,
        entry: step.id === entry,
        status,
        summary: summary.slice(0, 70),
      },
    };
  });
}
