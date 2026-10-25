import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Input, Modal, Select, Space, Tag } from "antd";
import { ReactFlow, Background, Controls, MiniMap } from "@xyflow/react";
import { streamEvents } from "../api";
import { graphEdges, graphNodes, type Run, type Workflow } from "./graph";
import { workflowNodeTypes } from "./WorkflowNode";
import "./workflow.css";

const statusNames: Record<string, string> = {
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已中断",
  idle: "等待执行",
  skipped: "未执行",
};
export function WorkflowRunView({
  workflow,
  record,
  models,
  defaultModel,
  onClose,
  onFinished,
}: {
  workflow: Workflow;
  record?: Run;
  models: Record<string, any>[];
  defaultModel: string;
  onClose: () => void;
  onFinished: () => void;
}) {
  const [run, setRun] = useState<Run>(record || { status: "idle", trace: [] }),
    [running, setRunning] = useState(false),
    [error, setError] = useState("");
  const [input, setInput] = useState(""),
    [model, setModel] = useState(defaultModel),
    [selected, setSelected] = useState(
      workflow.entry_id || workflow.steps[0]?.id,
    );
  const controller = useRef<AbortController | null>(null);
  const snapshot = run.workflow_snapshot || workflow;
  const nodes = useMemo(
    () =>
      graphNodes(
        snapshot,
        run.trace,
        !["idle", "running"].includes(run.status),
      ).map((n) => ({ ...n, selected: n.id === selected })),
    [snapshot, run, selected],
  );
  const edges = useMemo(() => graphEdges(snapshot, run.trace), [snapshot, run]);
  useEffect(() => () => controller.current?.abort(), []);
  const trace = run.trace.find((t) => t.step === selected),
    step = snapshot.steps.find((s) => s.id === selected);
  async function start() {
    setRunning(true);
    setError("");
    setRun({ status: "running", trace: [], workflow_snapshot: workflow });
    controller.current = new AbortController();
    try {
      await streamEvents(
        "/workflows/" + workflow.id + "/stream",
        { input, model_id: model },
        controller.current.signal,
        (event, data) => {
          if (event === "snapshot" || event === "done") {
            setRun(data);
            if (data.current_step) setSelected(data.current_step);
          }
        },
      );
    } catch (e) {
      if ((e as Error).name === "AbortError")
        setRun((prev) => ({
          ...prev,
          status: "cancelled",
          current_step: null,
          trace: prev.trace.map((t) =>
            t.status === "running" ? { ...t, status: "cancelled" } : t,
          ),
        }));
      else {
        setError((e as Error).message);
        setRun((prev) => ({ ...prev, status: "failed" }));
      }
    } finally {
      setRunning(false);
      controller.current = null;
      onFinished();
    }
  }
  return (
    <Modal
      open
      title={null}
      footer={null}
      closable={false}
      width="94vw"
      style={{ top: 25 }}
      styles={{ body: { padding: 0 } }}
      maskClosable={false}
      onCancel={() => {
        if (!running) onClose();
      }}
      className="workflow-modal"
    >
      <div className="wf-run">
        <div className="wf-editor-header">
          <div>
            <span className="eyebrow">WORKFLOW EXECUTION</span>
            <h2>{workflow.name}</h2>
          </div>
          <Space>
            <Tag
              color={
                run.status === "completed"
                  ? "green"
                  : run.status === "failed"
                    ? "red"
                    : run.status === "running"
                      ? "blue"
                      : "default"
              }
            >
              {statusNames[run.status]}
            </Tag>
            <Button disabled={running} onClick={onClose}>
              关闭运行视图
            </Button>
          </Space>
        </div>
        {!record && (
          <div className="wf-run-input">
            <Select
              aria-label="运行模型"
              value={model}
              onChange={setModel}
              disabled={running}
              options={models
                .filter((m) => m.enabled)
                .map((m) => ({ value: m.id, label: m.name }))}
            />
            <Input.TextArea
              aria-label="工作流输入"
              placeholder="输入流程需要处理的内容"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              autoSize={{ minRows: 1, maxRows: 4 }}
              disabled={running}
            />
            {running ? (
              <Button danger onClick={() => controller.current?.abort()}>
                停止执行
              </Button>
            ) : (
              <Button type="primary" onClick={() => void start()}>
                开始执行
              </Button>
            )}
          </div>
        )}
        {(error || run.error) && (
          <Alert type="error" showIcon message={error || run.error} />
        )}
        <div className="wf-run-body">
          <div className="wf-canvas" data-testid="workflow-run-canvas">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={workflowNodeTypes}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable
              onNodeClick={(_, node) => setSelected(node.id)}
              fitView
              minZoom={0.2}
              maxZoom={1.5}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={22} />
              <Controls showInteractive={false} />
              <MiniMap />
            </ReactFlow>
            <div className="wf-legend">
              <span>● 执行中</span>
              <span>● 已完成</span>
              <span>● 失败</span>
              <span>灰色：未执行分支</span>
            </div>
          </div>
          <aside className="wf-inspector">
            <h3>执行详情</h3>
            <p className="helper">点击节点查看输入配置、输出及工具调用。</p>
            {step && (
              <>
                <strong>{step.label || step.id}</strong>
                <p>
                  <Tag>{statusNames[trace?.status || "skipped"]}</Tag>
                </p>
                {trace?.attempts && <p>尝试次数：{trace.attempts}</p>}
                {trace?.matched !== undefined && (
                  <p>条件结果：{trace.matched ? "匹配" : "不匹配"}</p>
                )}
                {step.model_id && <p>模型：{step.model_id}</p>}
                {step.server_id && (
                  <p>
                    MCP：{step.server_id} / {step.tool_name}
                  </p>
                )}
                {trace?.output && (
                  <>
                    <label>节点输出</label>
                    <pre>{trace.output}</pre>
                  </>
                )}
                {trace?.events?.length ? (
                  <>
                    <label>内部调用记录</label>
                    {trace.events.map((event, i) => (
                      <div className="wf-tool-event" key={i}>
                        <b>{String(event.event)}</b>
                        <div>
                          {String(
                            event.tool_name || event.name || event.text || "",
                          )}
                        </div>
                        {typeof event.result === "string" && (
                          <details>
                            <summary>工具返回</summary>
                            <pre>{event.result}</pre>
                          </details>
                        )}
                      </div>
                    ))}
                  </>
                ) : null}
                <details>
                  <summary>节点配置</summary>
                  <pre>{JSON.stringify(step, null, 2)}</pre>
                </details>
              </>
            )}
            <h3 className="wf-result-title">最终输出</h3>
            {run.output ? (
              <pre data-testid="workflow-output">{run.output}</pre>
            ) : (
              <p className="helper">执行完成后显示结果。</p>
            )}
          </aside>
        </div>
      </div>
    </Modal>
  );
}
