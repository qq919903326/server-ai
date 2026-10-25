import { ReactFlow } from "@xyflow/react";
import { graphEdges, graphNodes, type Workflow } from "./graph";
import { workflowNodeTypes } from "./WorkflowNode";
import "./workflow.css";
export function WorkflowPreview({ workflow }: { workflow: Workflow }) {
  return (
    <div className="wf-mini-graph">
      <ReactFlow
        nodes={graphNodes(workflow)}
        edges={graphEdges(workflow)}
        nodeTypes={workflowNodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
      />
    </div>
  );
}
