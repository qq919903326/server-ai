import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { WorkflowNode } from "./graph";
import { typeNames } from "./graph";

export function WorkflowNodeCard({ data, selected }: NodeProps<WorkflowNode>) {
  const { step, status, entry, summary } = data;
  const names: Record<string, string> = {
    idle: "待运行",
    running: "执行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已中断",
    skipped: "未执行",
  };
  return (
    <div
      data-testid={"wf-node-" + step.id}
      className={`wf-node wf-${step.type} wf-status-${status} ${selected ? "wf-selected" : ""}`}
    >
      <Handle
        id="in"
        type="target"
        position={Position.Left}
        data-testid={"wf-in-" + step.id}
      />
      <div className="wf-node-meta">
        <span>{typeNames[step.type]}</span>
        {entry && <b>开始</b>}
      </div>
      <strong>{step.label || step.id}</strong>
      <small>{summary}</small>
      <div className="wf-node-footer">
        <code>{step.id}</code>
        <span>{names[status] || status}</span>
      </div>
      {step.type === "condition" && (
        <>
          <span className="wf-port-label wf-yes">匹配</span>
          <span className="wf-port-label wf-no">不匹配</span>
        </>
      )}
      <Handle
        id="next"
        type="source"
        position={Position.Right}
        style={{ top: step.type === "condition" ? "38%" : "50%" }}
        data-testid={"wf-out-" + step.id}
      />
      {step.type === "condition" && (
        <Handle
          id="otherwise"
          type="source"
          position={Position.Right}
          style={{ top: "76%", background: "#cb9356" }}
          data-testid={"wf-otherwise-" + step.id}
        />
      )}
    </div>
  );
}
export const workflowNodeTypes = { workflow: WorkflowNodeCard };
