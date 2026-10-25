import { useMemo, useState } from "react";
import {
  App,
  Alert,
  Button,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Tag,
} from "antd";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  type Connection,
  type NodeChange,
  type Edge,
} from "@xyflow/react";
import { api } from "../api";
import { uuid } from "../utils/id";
import {
  connectSteps,
  graphEdges,
  graphNodes,
  removeStep,
  typeNames,
  validateGraph,
  type Step,
  type StepType,
  type Workflow,
  type WorkflowNode,
} from "./graph";
import { workflowNodeTypes } from "./WorkflowNode";
import "@xyflow/react/dist/style.css";
import "./workflow.css";

type Props = {
  workflow: Workflow;
  models: Record<string, any>[];
  skills: Record<string, any>[];
  mcps: Record<string, any>[];
  onClose: () => void;
  onSaved: () => void;
};
export function WorkflowEditor(props: Props) {
  return (
    <ReactFlowProvider>
      <Editor {...props} />
    </ReactFlowProvider>
  );
}
function Editor({ workflow, models, skills, mcps, onClose, onSaved }: Props) {
  const { message } = App.useApp(),
    flow = useReactFlow();
  const [draft, setDraft] = useState<Workflow>(() =>
    JSON.parse(JSON.stringify(workflow)),
  );
  const [selected, setSelected] = useState(
      workflow.entry_id || workflow.steps[0]?.id,
    ),
    [edgeId, setEdgeId] = useState<string>();
  const [saving, setSaving] = useState(false),
    [jsonOpen, setJsonOpen] = useState(false),
    [jsonText, setJsonText] = useState("");
  const [argumentDrafts, setArgumentDrafts] = useState<
    Record<string, { text: string; error: boolean }>
  >({});
  const [dirty, setDirty] = useState(false);
  const step = draft.steps.find((s) => s.id === selected),
    edges = useMemo(() => graphEdges(draft), [draft]);
  const nodes = useMemo(
    () => graphNodes(draft).map((n) => ({ ...n, selected: n.id === selected })),
    [draft, selected],
  );
  const argsText =
    argumentDrafts[selected]?.text ??
    JSON.stringify(step?.arguments || {}, null, 2);
  const argsError = argumentDrafts[selected]?.error ?? false;
  function change(next: Workflow | ((previous: Workflow) => Workflow)) {
    setDraft(next);
    setDirty(true);
  }
  function patch(value: Partial<Step>) {
    change((previous) => ({
      ...previous,
      steps: previous.steps.map((s) =>
        s.id === selected ? { ...s, ...value } : s,
      ),
    }));
  }
  function add(type: StepType, position?: { x: number; y: number }) {
    if (draft.steps.length >= 40) {
      message.warning("最多 40 个节点");
      return;
    }
    const id = type + "_" + uuid().slice(0, 6);
    const step: Step = {
      id,
      type,
      label: typeNames[type],
      prompt: "",
      model_id: "",
      skill_ids: [],
      mcp_ids: [],
      server_id: "",
      tool_name: "",
      arguments: {},
      contains: "关键词",
      next: null,
      otherwise: null,
      retries: 0,
      position:
        position ||
        flow.screenToFlowPosition({
          x: window.innerWidth * 0.5,
          y: window.innerHeight * 0.45,
        }),
    };
    change({
      ...draft,
      entry_id: draft.entry_id || draft.steps[0]?.id || id,
      steps: [...draft.steps, step],
    });
    setSelected(id);
  }
  function connect(connection: Connection) {
    try {
      change(connectSteps(draft, connection));
    } catch (e) {
      message.error((e as Error).message);
    }
  }
  function nodeChanges(changes: NodeChange<WorkflowNode>[]) {
    const positions = new Map<string, { x: number; y: number }>();
    for (const c of changes)
      if (c.type === "position" && c.position) positions.set(c.id, c.position);
    if (positions.size)
      change({
        ...draft,
        steps: draft.steps.map((s) =>
          positions.has(s.id) ? { ...s, position: positions.get(s.id) } : s,
        ),
      });
  }
  function deleteEdges(deleted: Edge[]) {
    change((previous) => ({
      ...previous,
      steps: previous.steps.map((s) => {
        const n = { ...s };
        for (const edge of deleted)
          if (edge.source === s.id)
            n[edge.sourceHandle === "otherwise" ? "otherwise" : "next"] = null;
        return n;
      }),
    }));
    setEdgeId(undefined);
  }
  function close() {
    if (dirty)
      Modal.confirm({
        title: "放弃未保存的修改？",
        content: "节点与连线的修改尚未保存。",
        onOk: onClose,
      });
    else onClose();
  }
  async function save() {
    try {
      validateGraph(draft);
      if (!draft.name.trim()) throw new Error("请输入工作流名称");
      if (
        draft.steps.some(
          (s) => s.type === "tool" && argumentDrafts[s.id]?.error,
        )
      )
        throw new Error("请先修正工具参数 JSON");
      for (const s of draft.steps)
        if (s.type === "tool" && (!s.server_id || !s.tool_name))
          throw new Error((s.label || s.id) + " 需要选择 MCP 服务和工具");
      setSaving(true);
      await api("/workflows/" + draft.id, "PUT", draft);
      setDirty(false);
      message.success("工作流已保存");
      onSaved();
      onClose();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  const options = (items: Record<string, any>[]) =>
    items.filter((i) => i.enabled).map((i) => ({ value: i.id, label: i.name }));
  return (
    <Modal
      open
      title={null}
      footer={null}
      closable={false}
      width="96vw"
      styles={{ body: { padding: 0 } }}
      style={{ top: 20 }}
      onCancel={close}
      maskClosable={false}
      className="workflow-modal"
    >
      <div className="wf-editor">
        <div className="wf-editor-header">
          <div>
            <span className="eyebrow">WORKFLOW DESIGNER</span>
            <Input
              aria-label="工作流名称"
              value={draft.name}
              onChange={(e) => change({ ...draft, name: e.target.value })}
            />
          </div>
          <Space>
            <Button
              onClick={() => {
                setJsonText(JSON.stringify(draft, null, 2));
                setJsonOpen(true);
              }}
            >
              JSON 配置
            </Button>
            <Button onClick={close}>关闭编辑器</Button>
            <Button type="primary" loading={saving} onClick={() => void save()}>
              保存工作流
            </Button>
          </Space>
        </div>
        <div className="wf-editor-body">
          <aside className="wf-palette">
            <h3>添加节点</h3>
            <p>点击添加，或拖入画布</p>
            {(Object.keys(typeNames) as StepType[]).map((type) => (
              <button
                key={type}
                className={"wf-palette-item wf-" + type}
                draggable
                onDragStart={(e) =>
                  e.dataTransfer.setData("workflow-node", type)
                }
                onClick={() => add(type)}
                data-testid={"add-" + type}
              >
                ＋ {typeNames[type]}
              </button>
            ))}
            <Alert
              type="info"
              message="连接调用关系"
              description="从右侧端口拖到目标节点左侧端口。条件节点有两条分支；点击连线可删除。"
            />
            <div className="wf-palette-foot">
              {draft.steps.length} / 40 个节点
              <br />
              从带「开始」标记的节点执行
            </div>
          </aside>
          <div
            className="wf-canvas"
            data-testid="workflow-canvas"
            onDragOver={(e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = "move";
            }}
            onDrop={(e) => {
              e.preventDefault();
              const type = e.dataTransfer.getData("workflow-node") as StepType;
              if (type in typeNames)
                add(
                  type,
                  flow.screenToFlowPosition({ x: e.clientX, y: e.clientY }),
                );
            }}
          >
            <ReactFlow
              nodes={nodes}
              edges={edges.map((e) => ({ ...e, selected: e.id === edgeId }))}
              nodeTypes={workflowNodeTypes}
              onNodesChange={nodeChanges}
              onConnect={connect}
              onNodeDragStart={(_, node) => {
                setSelected(node.id);
                setEdgeId(undefined);
              }}
              onNodeClick={(_, node) => {
                setSelected(node.id);
                setEdgeId(undefined);
              }}
              onEdgeClick={(_, edge) => {
                setEdgeId(edge.id);
                setSelected("");
              }}
              onPaneClick={() => {
                setSelected("");
                setEdgeId(undefined);
              }}
              onEdgesDelete={deleteEdges}
              onNodesDelete={(deleted) => {
                change((previous) => {
                  let next = previous;
                  for (const node of deleted) next = removeStep(next, node.id);
                  return next;
                });
                setSelected("");
              }}
              fitView
              minZoom={0.2}
              maxZoom={1.6}
              deleteKeyCode={["Backspace", "Delete"]}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={22} color="#d8dfed" />
              <Controls />
              <MiniMap pannable zoomable nodeColor="#c6ceee" />
            </ReactFlow>
          </div>
          <aside className="wf-inspector">
            {step ? (
              <>
                <div className="wf-inspector-heading">
                  <h3>节点配置</h3>
                  <Tag>{typeNames[step.type]}</Tag>
                </div>
                <label>节点名称</label>
                <Input
                  aria-label="节点名称"
                  value={step.label || ""}
                  onChange={(e) => patch({ label: e.target.value })}
                />
                <label>节点 ID</label>
                <Input value={step.id} disabled />
                <Button
                  size="small"
                  disabled={(draft.entry_id || draft.steps[0]?.id) === step.id}
                  onClick={() => change({ ...draft, entry_id: step.id })}
                >
                  设为开始节点
                </Button>
                <label>节点类型</label>
                <Select
                  aria-label="节点类型"
                  value={step.type}
                  options={Object.entries(typeNames).map(([value, label]) => ({
                    value,
                    label,
                  }))}
                  onChange={(type) =>
                    patch({
                      type,
                      otherwise: type === "condition" ? step.otherwise : null,
                    })
                  }
                />
                {(step.type === "prompt" || step.type === "template") && (
                  <>
                    <label>
                      {step.type === "prompt" ? "提示词" : "模板内容"}
                    </label>
                    <Input.TextArea
                      aria-label="节点提示词"
                      rows={6}
                      value={step.prompt || ""}
                      onChange={(e) => patch({ prompt: e.target.value })}
                    />
                    <p className="helper">
                      变量：{"{{input}}、{{previous}}、{{steps.节点ID}}"}
                    </p>
                  </>
                )}
                {step.type === "prompt" && (
                  <>
                    <label>调用模型</label>
                    <Select
                      allowClear
                      placeholder="使用运行时选择的模型"
                      value={step.model_id || undefined}
                      options={options(models)}
                      onChange={(value) => patch({ model_id: value || "" })}
                    />
                    <label>Skills</label>
                    <Select
                      mode="multiple"
                      value={step.skill_ids || []}
                      options={options(skills)}
                      onChange={(skill_ids) => patch({ skill_ids })}
                    />
                    <label>Agent 可读取的文件库与工具</label>
                    <Select
                      mode="multiple"
                      value={step.mcp_ids || []}
                      options={[
                        {
                          label: "本地文件库",
                          options: options(
                            mcps.filter((m) => m.service_type === "project"),
                          ),
                        },
                        {
                          label: "MCP 工具服务",
                          options: options(
                            mcps.filter((m) => m.service_type !== "project"),
                          ),
                        },
                      ]}
                      onChange={(mcp_ids) => patch({ mcp_ids })}
                    />
                  </>
                )}
                {step.type === "condition" && (
                  <>
                    <label>上一步输出包含</label>
                    <Input
                      aria-label="匹配关键词"
                      value={step.contains || ""}
                      onChange={(e) => patch({ contains: e.target.value })}
                    />
                    <p className="helper">匹配走绿色端口，不匹配走橙色端口。</p>
                  </>
                )}
                {step.type === "tool" && (
                  <>
                    <label>文件库或 MCP 服务</label>
                    <Select
                      placeholder="选择文件库或远程工具"
                      value={step.server_id || undefined}
                      options={[
                        {
                          label: "本地文件库",
                          options: options(
                            mcps.filter((m) => m.service_type === "project"),
                          ),
                        },
                        {
                          label: "MCP 工具服务",
                          options: options(
                            mcps.filter((m) => m.service_type !== "project"),
                          ),
                        },
                      ]}
                      onChange={(server_id) =>
                        patch({ server_id, tool_name: "" })
                      }
                    />
                    <label>调用工具</label>
                    <Select
                      value={step.tool_name || undefined}
                      placeholder="选择已授权的工具"
                      options={(
                        mcps.find((m) => m.id === step.server_id)
                          ?.allowed_tools || []
                      ).map((name: string) => ({ value: name, label: name }))}
                      onChange={(tool_name) => patch({ tool_name })}
                    />
                    <label>工具参数 JSON</label>
                    <Input.TextArea
                      aria-label="工具参数 JSON"
                      rows={7}
                      status={argsError ? "error" : undefined}
                      value={argsText}
                      onChange={(e) => {
                        const text = e.target.value;
                        try {
                          const args = JSON.parse(e.target.value);
                          if (
                            !args ||
                            Array.isArray(args) ||
                            typeof args !== "object"
                          )
                            throw new Error();
                          patch({ arguments: args });
                          setArgumentDrafts((previous) => ({
                            ...previous,
                            [selected]: { text, error: false },
                          }));
                        } catch {
                          setArgumentDrafts((previous) => ({
                            ...previous,
                            [selected]: { text, error: true },
                          }));
                        }
                      }}
                    />
                    {argsError && (
                      <p className="wf-error">参数必须是 JSON 对象</p>
                    )}
                  </>
                )}
                <label>失败重试次数</label>
                <InputNumber
                  min={0}
                  max={3}
                  value={step.retries || 0}
                  onChange={(value) => patch({ retries: value || 0 })}
                />
                <p className="helper">
                  有写入副作用的工具保持 0，避免重复执行。
                </p>
                <Button
                  danger
                  onClick={() => {
                    change(removeStep(draft, step.id));
                    setSelected("");
                  }}
                >
                  删除节点
                </Button>
              </>
            ) : edgeId ? (
              <>
                <h3>连线配置</h3>
                <p>
                  {edges.find((e) => e.id === edgeId)?.source} →{" "}
                  {edges.find((e) => e.id === edgeId)?.target}
                </p>
                <Button
                  danger
                  onClick={() =>
                    deleteEdges(edges.filter((e) => e.id === edgeId))
                  }
                >
                  删除连线
                </Button>
              </>
            ) : (
              <>
                <h3>工作流配置</h3>
                <label>流程说明</label>
                <Input.TextArea
                  rows={4}
                  value={draft.description || ""}
                  onChange={(e) =>
                    change({ ...draft, description: e.target.value })
                  }
                />
                <p className="helper">
                  点击节点编辑参数，拖动节点调整布局。未连到开始节点的步骤不会执行。
                </p>
                <Button
                  onClick={() => {
                    change({
                      ...draft,
                      steps: draft.steps.map((s) => ({ ...s, position: null })),
                    });
                    setTimeout(() => flow.fitView({ padding: 0.2 }), 0);
                  }}
                >
                  自动排列
                </Button>
              </>
            )}
          </aside>
        </div>
      </div>
      <Modal
        title="JSON 配置 / 导入"
        open={jsonOpen}
        onCancel={() => setJsonOpen(false)}
        width={800}
        okText="应用到画布"
        onOk={() => {
          try {
            const value = JSON.parse(jsonText);
            validateGraph(value);
            change({ ...value, id: draft.id });
            setArgumentDrafts({});
            setSelected(value.entry_id || value.steps[0].id);
            setJsonOpen(false);
            setTimeout(() => flow.fitView(), 0);
          } catch (e) {
            message.error((e as Error).message);
          }
        }}
      >
        <Input.TextArea
          aria-label="工作流 JSON"
          className="code-input"
          rows={22}
          value={jsonText}
          onChange={(e) => setJsonText(e.target.value)}
        />
      </Modal>
    </Modal>
  );
}
