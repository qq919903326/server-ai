import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  App as AntApp,
  Alert,
  Button,
  Card,
  ConfigProvider,
  Drawer,
  Empty,
  Form,
  Input,
  Layout,
  Menu,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import zhCN from "antd/locale/zh_CN";
import {
  ApiOutlined,
  AppstoreOutlined,
  ArrowUpOutlined,
  CheckCircleOutlined,
  CodeOutlined,
  DeleteOutlined,
  EditOutlined,
  FileTextOutlined,
  HistoryOutlined,
  KeyOutlined,
  MessageOutlined,
  NodeIndexOutlined,
  PlusOutlined,
  RobotOutlined,
  SettingOutlined,
  StopOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, streamChat } from "./api";
import "./style.css";
import "./responsive.css";

type RecordData = { [key: string]: any };
const titles: Record<string, [string, string]> = {
  chat: ["对话工作台", "让模型、技能与工具协同完成任务"],
  models: ["模型连接", "统一管理 DeepSeek、GPT、MiMo 和兼容服务"],
  mcp: ["MCP 工具", "连接外部能力，明确每项工具的执行权限"],
  skills: ["Skills 技能", "将可复用的任务方法，变成助手的能力"],
  workflows: ["Workflow 工作流", "用明确的步骤，组织可重复执行的任务"],
  runs: ["运行记录", "追踪每一次执行的结果与工具调用"],
};
const ids = () => crypto.randomUUID().replaceAll("-", "").slice(0, 12);
function Markdown({ text }: { text: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>;
}
function statusTag(status: string) {
  return (
    <Tag
      color={
        status === "completed"
          ? "green"
          : status === "failed"
            ? "red"
            : status === "running"
              ? "blue"
              : "default"
      }
    >
      {(
        {
          completed: "已完成",
          failed: "失败",
          running: "执行中",
          cancelled: "已中断",
        } as Record<string, string>
      )[status] || status}
    </Tag>
  );
}

function Console() {
  const { message } = AntApp.useApp();
  const [page, setPage] = useState("chat"),
    [loading, setLoading] = useState(false);
  const [connected, setConnected] = useState(false);
  const [models, setModels] = useState<RecordData[]>([]),
    [mcps, setMcps] = useState<RecordData[]>([]),
    [skills, setSkills] = useState<RecordData[]>([]),
    [workflows, setWorkflows] = useState<RecordData[]>([]),
    [runs, setRuns] = useState<RecordData[]>([]),
    [sessions, setSessions] = useState<RecordData[]>([]);
  const [model, setModel] = useState("deepseek"),
    [selectedSkills, setSelectedSkills] = useState<string[]>([]),
    [selectedMCP, setSelectedMCP] = useState<string[]>([]);
  const [sid, setSid] = useState<string>(),
    [messages, setMessages] = useState<RecordData[]>([]),
    [input, setInput] = useState(""),
    [busy, setBusy] = useState(false),
    [status, setStatus] = useState(""),
    [trace, setTrace] = useState<RecordData[]>([]);
  const [editor, setEditor] = useState<{
      kind: string;
      data: RecordData;
    } | null>(null),
    [form] = Form.useForm();
  const [detail, setDetail] = useState<RecordData | null>(null),
    [tokenOpen, setTokenOpen] = useState(false),
    [token, setToken] = useState(sessionStorage.getItem("ai-token") || "");
  const [workflowRun, setWorkflowRun] = useState<RecordData | null>(null),
    [workflowInput, setWorkflowInput] = useState(""),
    [workflowBusy, setWorkflowBusy] = useState(false);
  const controller = useRef<AbortController | null>(null),
    bottom = useRef<HTMLDivElement>(null);

  async function refresh() {
    setLoading(true);
    try {
      const data = await Promise.all(
        ["/models", "/mcp", "/skills", "/workflows", "/runs", "/sessions"].map(
          (p) => api(p),
        ),
      );
      setConnected(true);
      setModels(data[0]);
      setMcps(data[1]);
      setSelectedMCP((prev) =>
        prev.filter((id) =>
          data[1].some(
            (m: RecordData) =>
              m.id === id && m.enabled && m.allowed_tools?.length,
          ),
        ),
      );
      setSkills(data[2]);
      setWorkflows(data[3]);
      setRuns(data[4]);
      setSessions(data[5]);
    } catch (e) {
      setConnected(false);
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void refresh();
  }, []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, status]);
  const configured = models.filter((m) => m.has_key).length;

  function edit(kind: string, data?: RecordData) {
    const defaults: Record<string, RecordData> = {
      models: {
        id: ids(),
        name: "",
        provider: "custom",
        base_url: "https://api.openai.com/v1",
        model: "",
        key_env: "",
        enabled: true,
      },
      mcp: {
        id: ids(),
        name: "",
        transport: "http",
        url: "",
        token_env: "",
        allowed_tools: [],
        enabled: false,
      },
      skills: {
        id: ids(),
        name: "",
        description: "",
        content: "# 技能说明\n\n描述任务目标、执行步骤与输出要求。",
        enabled: true,
      },
      workflows: {
        id: ids(),
        name: "",
        description: "",
        steps: [
          {
            id: "first",
            type: "prompt",
            prompt: "请处理以下任务：\n{{input}}",
          },
        ],
      },
    };
    const value = { ...(data || defaults[kind]) };
    if (kind === "models") value.api_key = "";
    if (kind === "workflows")
      value.steps_json = JSON.stringify(value.steps, null, 2);
    setEditor({ kind, data: value });
    form.resetFields();
    form.setFieldsValue(value);
  }
  function connectDatabase(engine: "mysql" | "postgres") {
    edit("mcp", {
      id: engine + "-" + ids(),
      name: engine === "mysql" ? "MySQL" : "PostgreSQL",
      transport: "http",
      url: "",
      token_env: engine === "mysql" ? "MYSQL_MCP_TOKEN" : "POSTGRES_MCP_TOKEN",
      allowed_tools: ["list_tables", "describe_table", "query"],
      enabled: true,
    });
  }
  async function save() {
    if (!editor) return;
    try {
      const data = await form.validateFields();
      if (editor.kind === "workflows") data.steps = JSON.parse(data.steps_json);
      await api(`/${editor.kind}/${editor.data.id}`, "PUT", {
        ...editor.data,
        ...data,
      });
      setEditor(null);
      message.success("已保存");
      await refresh();
    } catch (e) {
      if (e instanceof Error) message.error(e.message);
    }
  }
  async function remove(kind: string, id: string) {
    try {
      await api(`/${kind}/${id}`, "DELETE");
      await refresh();
    } catch (e) {
      message.error((e as Error).message);
    }
  }
  async function test(kind: string, item: RecordData) {
    const close = message.loading("正在测试连接…", 0);
    try {
      const result = await api(`/${kind}/${item.id}/test`, "POST");
      setDetail({ ...result, name: item.name });
      message.success("连接成功");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      close();
    }
  }
  async function send(value = input) {
    if (!value.trim() || busy) return;
    setInput("");
    setBusy(true);
    setTrace([]);
    setStatus("连接模型中…");
    setMessages((prev) => [...prev, { role: "user", content: value }]);
    let responseStarted = false;
    controller.current = new AbortController();
    try {
      await streamChat(
        {
          model_id: model,
          message: value,
          session_id: sid,
          skill_ids: selectedSkills,
          mcp_ids: selectedMCP,
        },
        controller.current.signal,
        (event, data) => {
          if (event === "session") setSid(data.session_id);
          if (event === "status") setStatus(data.text);
          if (event === "tool_start" || event === "tool_end") {
            setTrace((prev) => [...prev, { event, ...data }]);
            setStatus(
              event === "tool_start"
                ? "正在调用工具…"
                : "工具执行完成，正在整理…",
            );
          }
          if (event === "delta") {
            const started = responseStarted;
            responseStarted = true;
            setMessages((prev) =>
              started
                ? prev.map((m, i) =>
                    i === prev.length - 1
                      ? { ...m, content: m.content + data.text }
                      : m,
                  )
                : [...prev, { role: "assistant", content: data.text }],
            );
          }
          if (event === "tool_start") responseStarted = false;
          if (event === "answer") {
            const started = responseStarted;
            setMessages((prev) =>
              started
                ? prev.map((m, i) =>
                    i === prev.length - 1 ? { ...m, content: data.text } : m,
                  )
                : [...prev, { role: "assistant", content: data.text }],
            );
          }
          if (event === "error") {
            message.error(data.message);
            setMessages((prev) => [
              ...prev,
              { role: "error", content: data.message },
            ]);
          }
        },
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError")
        message.error((e as Error).message);
    } finally {
      setBusy(false);
      setStatus("");
      controller.current = null;
      void refresh();
    }
  }
  async function openSession(item: RecordData) {
    if (busy) return;
    try {
      const data = await api("/sessions/" + item.id);
      setSid(item.id);
      setMessages(data.messages);
      setTrace([]);
    } catch (e) {
      message.error((e as Error).message);
    }
  }
  async function executeWorkflow() {
    if (!workflowRun) return;
    setWorkflowBusy(true);
    try {
      const result = await api(
        "/workflows/" + workflowRun.id + "/run",
        "POST",
        { input: workflowInput, model_id: model },
      );
      setDetail(result);
      setWorkflowRun(null);
      await refresh();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setWorkflowBusy(false);
    }
  }
  const actions = (kind: string, item: RecordData) => (
    <Space>
      <Tooltip title="编辑">
        <Button icon={<EditOutlined />} onClick={() => edit(kind, item)} />
      </Tooltip>
      <Popconfirm
        title="确认删除这条记录？"
        onConfirm={() => remove(kind, item.id)}
      >
        <Button danger icon={<DeleteOutlined />} />
      </Popconfirm>
    </Space>
  );

  return (
    <Layout className="app-layout">
      <Layout.Sider
        width={228}
        className="sidebar"
        breakpoint="lg"
        collapsedWidth={68}
      >
        <div className="brand">
          <span className="brand-icon">
            <ThunderboltOutlined />
          </span>
          <span>
            AI Studio<small>SERVER CONSOLE</small>
          </span>
        </div>
        <div className="nav-label">工作空间</div>
        <Menu
          selectedKeys={[page]}
          onClick={({ key }) => {
            setPage(key);
            void refresh();
          }}
          items={[
            { key: "chat", icon: <MessageOutlined />, label: "对话工作台" },
            { key: "models", icon: <RobotOutlined />, label: "模型连接" },
            { key: "mcp", icon: <ApiOutlined />, label: "MCP 工具" },
            { key: "skills", icon: <AppstoreOutlined />, label: "Skills 技能" },
            {
              key: "workflows",
              icon: <NodeIndexOutlined />,
              label: "Workflow 工作流",
            },
            { key: "runs", icon: <HistoryOutlined />, label: "运行记录" },
          ]}
        />
        <div className="sidebar-foot">
          <span className="online-dot" /> 本地服务 · v0.1.0
          <div>Python + React</div>
        </div>
      </Layout.Sider>
      <Layout className="main-layout">
        <header className="topbar">
          <span>
            工作空间 <span className="slash">/</span> <b>{titles[page][0]}</b>
          </span>
          <Space>
            <Tag bordered={false} color={connected ? "green" : "default"}>
              {connected ? "服务已连接" : "等待连接"}
            </Tag>
            <Button icon={<KeyOutlined />} onClick={() => setTokenOpen(true)}>
              访问令牌
            </Button>
          </Space>
        </header>
        <main
          className={"main-content " + (page === "chat" ? "chat-main" : "")}
        >
          <div className="page-heading">
            <div>
              <div className="eyebrow">AI WORKSPACE</div>
              <h1>{titles[page][0]}</h1>
              <p>{titles[page][1]}</p>
            </div>
            {page !== "chat" && page !== "runs" && (
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => edit(page)}
              >
                新建
                {page === "models"
                  ? "模型"
                  : page === "mcp"
                    ? "连接"
                    : page === "skills"
                      ? "技能"
                      : "工作流"}
              </Button>
            )}
          </div>
          {page === "chat" ? (
            <div className="chat-layout">
              <section className="conversation-panel">
                <div className="conversation-toolbar">
                  <Space>
                    <span className="online-dot" />
                    <Select
                      aria-label="选择模型"
                      value={model}
                      onChange={setModel}
                      options={models
                        .filter((m) => m.enabled)
                        .map((m) => ({ value: m.id, label: m.name }))}
                      style={{ width: 200 }}
                    />
                    <Tag>Agent</Tag>
                  </Space>
                  <Button
                    disabled={busy}
                    icon={<PlusOutlined />}
                    onClick={() => {
                      setSid(undefined);
                      setMessages([]);
                      setTrace([]);
                    }}
                  >
                    新对话
                  </Button>
                </div>
                <div className="messages">
                  {!messages.length ? (
                    <div className="welcome">
                      <div className="welcome-icon">
                        <ThunderboltOutlined />
                      </div>
                      <span className="eyebrow">YOUR AI, CONNECTED</span>
                      <h2>从一个想法开始</h2>
                      <p>
                        选择模型，搭配技能与工具，
                        <br />让 AI 帮你把事情做完。
                      </p>
                      <div className="suggestions">
                        {[
                          "帮我梳理一个项目的实施步骤",
                          "比较两个技术方案的优缺点",
                          "将我的想法整理成清晰的文案",
                        ].map((text, i) => (
                          <button key={text} onClick={() => setInput(text)}>
                            <span>{["01", "02", "03"][i]}</span>
                            {text}
                            <ArrowUpOutlined />
                          </button>
                        ))}
                      </div>
                      {!configured && (
                        <Alert
                          type="info"
                          showIcon
                          message="先连接你的模型"
                          description={
                            <span>
                              在「模型连接」中配置 API Key，即可开始真实对话。
                              <a onClick={() => setPage("models")}> 去配置 →</a>
                            </span>
                          }
                        />
                      )}
                    </div>
                  ) : (
                    messages.map((m, i) => (
                      <div className={"message-row " + m.role} key={i}>
                        <div className="avatar">
                          {m.role === "user" ? "你" : <ThunderboltOutlined />}
                        </div>
                        <div className="message-body">
                          <div className="message-label">
                            {m.role === "user"
                              ? "你"
                              : m.role === "error"
                                ? "执行提示"
                                : "AI 助手"}
                          </div>
                          <Markdown text={m.content} />
                        </div>
                      </div>
                    ))
                  )}
                  {busy && (
                    <div className="thinking">
                      <Spin size="small" /> {status}
                    </div>
                  )}
                  <div ref={bottom} />
                </div>
                <div className="composer">
                  <Input.TextArea
                    aria-label="消息输入"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="输入你的问题或任务…"
                    autoSize={{ minRows: 2, maxRows: 7 }}
                    onKeyDown={(e) => {
                      if (
                        e.key === "Enter" &&
                        !e.shiftKey &&
                        !e.nativeEvent.isComposing
                      ) {
                        e.preventDefault();
                        void send();
                      }
                    }}
                  />
                  <div className="composer-footer">
                    <span>Enter 发送 · Shift + Enter 换行</span>
                    {busy ? (
                      <Button
                        icon={<StopOutlined />}
                        onClick={() => controller.current?.abort()}
                      >
                        停止
                      </Button>
                    ) : (
                      <Button
                        type="primary"
                        disabled={!input.trim()}
                        icon={<ArrowUpOutlined />}
                        onClick={() => void send()}
                      >
                        发送
                      </Button>
                    )}
                  </div>
                </div>
              </section>
              <aside className="context-panel">
                <div className="context-title">
                  <SettingOutlined /> 本次对话配置
                </div>
                <label>Skills 技能</label>
                <Select
                  mode="multiple"
                  placeholder="选择任务技能"
                  value={selectedSkills}
                  onChange={setSelectedSkills}
                  options={skills
                    .filter((s) => s.enabled)
                    .map((s) => ({ value: s.id, label: s.name }))}
                />
                <label>MCP 工具服务</label>
                <Select
                  aria-label="MCP 工具服务"
                  mode="multiple"
                  placeholder={
                    mcps.length ? "选择工具服务" : "请先配置数据库或其他 MCP"
                  }
                  notFoundContent="尚未配置远程 MCP 服务"
                  value={selectedMCP}
                  onChange={setSelectedMCP}
                  options={mcps.map((m) => ({
                    value: m.id,
                    label:
                      m.name +
                      (!m.enabled
                        ? "（未启用）"
                        : !m.allowed_tools?.length
                          ? "（未授权工具）"
                          : ""),
                    disabled: !m.enabled || !m.allowed_tools?.length,
                  }))}
                />
                <Button type="link" size="small" onClick={() => setPage("mcp")}>
                  配置数据库 MCP
                </Button>
                <p className="helper">仅所选技能和工具会参与本次任务。</p>
                <div className="context-title history-heading">
                  <HistoryOutlined /> 最近对话
                </div>
                <div className="session-list">
                  {sessions.length ? (
                    sessions.slice(0, 12).map((s) => (
                      <button
                        className={sid === s.id ? "selected" : ""}
                        key={s.id}
                        disabled={busy}
                        onClick={() => void openSession(s)}
                      >
                        <MessageOutlined />
                        <span>{s.title}</span>
                      </button>
                    ))
                  ) : (
                    <p className="helper">开始对话后会自动保存</p>
                  )}
                </div>
                {trace.length > 0 && (
                  <>
                    <div className="context-title">执行过程</div>
                    <div className="trace-list">
                      {trace.map((t, i) => (
                        <div key={i}>
                          <Tag
                            color={t.event === "tool_end" ? "green" : "blue"}
                          >
                            {t.event === "tool_end" ? "完成" : "调用"}
                          </Tag>
                          <code>{t.name}</code>
                          {t.result && (
                            <details>
                              <summary>查看结果</summary>
                              <pre>{t.result}</pre>
                            </details>
                          )}
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </aside>
            </div>
          ) : (
            <Spin spinning={loading}>
              {page === "models" && (
                <>
                  <div className="stat-strip">
                    <div>
                      <span>模型连接</span>
                      <strong>{models.length}</strong>
                    </div>
                    <div>
                      <span>已配置密钥</span>
                      <strong>{configured}</strong>
                    </div>
                    <div>
                      <span>推理方式</span>
                      <strong className="small-stat">云端 API</strong>
                    </div>
                  </div>
                  <div className="card-grid">
                    {models.map((item) => (
                      <Card key={item.id} className="resource-card">
                        <div className="resource-heading">
                          <div className={"provider-icon " + item.provider}>
                            {item.provider === "deepseek" ? (
                              "D"
                            ) : item.provider === "openai" ? (
                              "G"
                            ) : item.provider === "mimo" ? (
                              "M"
                            ) : (
                              <RobotOutlined />
                            )}
                          </div>
                          <div>
                            <h3>{item.name}</h3>
                            <span>{item.provider.toUpperCase()}</span>
                          </div>
                          {item.enabled ? (
                            <Tag color="green">已启用</Tag>
                          ) : (
                            <Tag>已停用</Tag>
                          )}
                        </div>
                        <div className="model-name">{item.model}</div>
                        <div className="endpoint">{item.base_url}</div>
                        <div className="key-state">
                          <span
                            className={
                              "online-dot " + (!item.has_key ? "offline" : "")
                            }
                          />
                          {item.has_key ? "API Key 已配置" : "等待配置 API Key"}
                        </div>
                        <div className="card-actions">
                          <Button onClick={() => void test("models", item)}>
                            测试连接
                          </Button>
                          {actions("models", item)}
                        </div>
                      </Card>
                    ))}
                  </div>
                </>
              )}
              {page === "mcp" && (
                <>
                  <Alert
                    className="section-alert"
                    type="info"
                    showIcon
                    message="每个连接独立授权工具"
                    description="填写远程 MCP HTTP 地址，测试连接查看工具名称，再编辑白名单并启用。"
                  />
                  <div className="section-tools">
                    <span>
                      数据库连接模板：填入已部署的远程 MCP
                      服务地址，保存后即可在对话中选择。
                    </span>
                    <Space wrap>
                      <Button onClick={() => connectDatabase("mysql")}>
                        连接 MySQL
                      </Button>
                      <Button onClick={() => connectDatabase("postgres")}>
                        连接 PostgreSQL
                      </Button>
                    </Space>
                  </div>
                  {!mcps.length && (
                    <Card>
                      <Empty description="尚未配置远程 MCP 服务">
                        <Button type="primary" onClick={() => edit("mcp")}>
                          添加远程 MCP
                        </Button>
                      </Empty>
                    </Card>
                  )}
                  <div className="card-grid">
                    {mcps.map((item) => (
                      <Card key={item.id} className="resource-card">
                        <div className="resource-heading">
                          <div className="provider-icon mcp">
                            <ApiOutlined />
                          </div>
                          <div>
                            <h3>{item.name}</h3>
                            <span>REMOTE HTTP</span>
                          </div>
                          <Tag color={item.enabled ? "green" : "default"}>
                            {item.enabled ? "已启用" : "未启用"}
                          </Tag>
                        </div>
                        <div className="endpoint tall">{item.url}</div>
                        <p className="helper">
                          已授权 {item.allowed_tools?.length || 0} 个工具
                        </p>
                        <div className="card-actions">
                          <Button onClick={() => void test("mcp", item)}>
                            测试与发现工具
                          </Button>
                          {actions("mcp", item)}
                        </div>
                      </Card>
                    ))}
                  </div>
                </>
              )}
              {page === "skills" && (
                <>
                  <div className="section-tools">
                    <span>
                      技能使用 Markdown 编写，选中后加载到模型上下文。
                    </span>
                    <Upload
                      accept=".md"
                      showUploadList={false}
                      beforeUpload={async (file) => {
                        const content = await file.text();
                        edit("skills", {
                          id: ids(),
                          name: file.name.replace(/\.md$/i, ""),
                          description: "导入的 Markdown 技能",
                          content,
                          enabled: true,
                        });
                        return false;
                      }}
                    >
                      <Button icon={<FileTextOutlined />}>导入 SKILL.md</Button>
                    </Upload>
                  </div>
                  <div className="card-grid">
                    {skills.map((item) => (
                      <Card key={item.id} className="resource-card">
                        <div className="resource-heading">
                          <div className="provider-icon skill">
                            <AppstoreOutlined />
                          </div>
                          <div>
                            <h3>{item.name}</h3>
                            <span>MARKDOWN SKILL</span>
                          </div>
                          <Tag color={item.enabled ? "green" : "default"}>
                            {item.enabled ? "已启用" : "已停用"}
                          </Tag>
                        </div>
                        <p className="skill-description">
                          {item.description || "可复用的任务说明"}
                        </p>
                        <div className="skill-preview">
                          {item.content.slice(0, 180)}
                        </div>
                        <div className="card-actions">
                          <Button
                            onClick={() =>
                              setDetail({ ...item, markdown: true })
                            }
                          >
                            查看说明
                          </Button>
                          {actions("skills", item)}
                        </div>
                      </Card>
                    ))}
                  </div>
                </>
              )}
              {page === "workflows" && (
                <>
                  <Alert
                    className="section-alert"
                    showIcon
                    type="info"
                    message="配置式工作流"
                    description="支持模型、模板、条件分支和 MCP 工具步骤，通过 next / otherwise 连接；每步可配置重试，执行记录自动保存。"
                  />
                  <div className="card-grid">
                    {workflows.map((item) => (
                      <Card key={item.id} className="resource-card">
                        <div className="resource-heading">
                          <div className="provider-icon flow">
                            <NodeIndexOutlined />
                          </div>
                          <div>
                            <h3>{item.name}</h3>
                            <span>{item.steps.length} 个步骤</span>
                          </div>
                        </div>
                        <p className="skill-description">
                          {item.description || "按配置执行任务"}
                        </p>
                        <div className="flow-preview">
                          {item.steps.map((s: RecordData, i: number) => (
                            <React.Fragment key={s.id}>
                              {i > 0 && <span>→</span>}
                              <Tag>{s.id}</Tag>
                            </React.Fragment>
                          ))}
                        </div>
                        <div className="card-actions">
                          <Button
                            type="primary"
                            ghost
                            onClick={() => {
                              setWorkflowRun(item);
                              setWorkflowInput("");
                            }}
                          >
                            运行流程
                          </Button>
                          {actions("workflows", item)}
                        </div>
                      </Card>
                    ))}
                  </div>
                </>
              )}
              {page === "runs" && (
                <Card className="table-card">
                  <Table
                    rowKey="id"
                    dataSource={runs}
                    pagination={{ pageSize: 10 }}
                    locale={{ emptyText: <Empty description="尚无运行记录" /> }}
                    columns={[
                      { title: "任务", dataIndex: "name", ellipsis: true },
                      {
                        title: "类型",
                        dataIndex: "kind",
                        render: (v: string) => (
                          <Tag>{v === "chat" ? "对话" : "Workflow"}</Tag>
                        ),
                      },
                      { title: "状态", dataIndex: "status", render: statusTag },
                      {
                        title: "开始时间",
                        dataIndex: "started_at",
                        render: (v: string) => new Date(v).toLocaleString(),
                      },
                      {
                        title: "操作",
                        render: (_: unknown, r: RecordData) => (
                          <Button onClick={() => setDetail(r)}>查看详情</Button>
                        ),
                      },
                    ]}
                  />
                </Card>
              )}
            </Spin>
          )}
          {page !== "chat" && (
            <footer className="page-footer">
              <CheckCircleOutlined /> 配置保存在当前服务器 ·
              密钥仅用于服务端调用
            </footer>
          )}
        </main>
      </Layout>
      <Modal
        title="访问令牌"
        open={tokenOpen}
        onCancel={() => setTokenOpen(false)}
        onOk={() => {
          sessionStorage.setItem("ai-token", token);
          setTokenOpen(false);
          void refresh();
        }}
      >
        <p>
          如果服务器配置了
          APP_TOKEN，请填写对应值。令牌仅保存在当前标签页会话中。
        </p>
        <Input.Password
          value={token}
          onChange={(e) => setToken(e.target.value)}
          autoComplete="off"
        />
      </Modal>
      <Drawer
        title={editor ? `编辑${titles[editor.kind]?.[0] || ""}` : ""}
        open={!!editor}
        onClose={() => setEditor(null)}
        width={640}
        extra={
          <Button type="primary" onClick={() => void save()}>
            保存配置
          </Button>
        }
      >
        {editor && (
          <Form form={form} layout="vertical">
            <Form.Item label="ID">
              <Input value={editor.data.id} disabled />
            </Form.Item>
            <Form.Item
              name="name"
              label="名称"
              rules={[{ required: true, message: "请输入名称" }]}
            >
              <Input />
            </Form.Item>
            {editor.kind === "models" && (
              <>
                <Form.Item name="provider" label="服务商">
                  <Select
                    options={[
                      { value: "deepseek", label: "DeepSeek" },
                      { value: "openai", label: "OpenAI" },
                      { value: "mimo", label: "小米 MiMo" },
                      { value: "custom", label: "兼容接口" },
                    ]}
                  />
                </Form.Item>
                <Form.Item
                  name="base_url"
                  label="API 地址"
                  rules={[{ required: true }]}
                >
                  <Input placeholder="https://api.deepseek.com" />
                </Form.Item>
                <Form.Item
                  name="model"
                  label="模型名称"
                  rules={[{ required: true }]}
                >
                  <Input placeholder="填写账号可用的模型 ID" />
                </Form.Item>
                <Form.Item
                  name="api_key"
                  label="API Key"
                  extra="留空保留已保存的密钥；也可以通过环境变量配置。"
                >
                  <Input.Password
                    autoComplete="new-password"
                    placeholder={
                      editor.data.has_key ? "已配置，留空保留" : "输入 API Key"
                    }
                  />
                </Form.Item>
                <Form.Item name="key_env" label="密钥环境变量名">
                  <Input placeholder="例如 DEEPSEEK_API_KEY" />
                </Form.Item>
                <Form.Item
                  name="clear_key"
                  label="清除已保存密钥"
                  valuePropName="checked"
                >
                  <Switch />
                </Form.Item>
              </>
            )}
            {editor.kind === "mcp" && (
              <>
                <Alert
                  type="info"
                  showIcon
                  message="填写 MCP 服务地址"
                  description="这里连接已部署的远程 MCP 服务。数据库账号和密码配置在该 MCP 服务端；保存并启用连接后，可在聊天右侧选择。"
                  style={{ marginBottom: 20 }}
                />
                <Form.Item name="transport" label="连接方式">
                  <Select
                    options={[{ value: "http", label: "Streamable HTTP" }]}
                  />
                </Form.Item>
                <Form.Item
                  name="url"
                  label="远程 MCP URL"
                  rules={[{ required: true, message: "请输入远程服务地址" }]}
                >
                  <Input placeholder="https://example.com/mcp" />
                </Form.Item>
                <Form.Item name="token_env" label="认证 Token 的环境变量名">
                  <Input placeholder="MY_MCP_TOKEN" />
                </Form.Item>
                <Form.Item
                  name="allowed_tools"
                  label="工具白名单"
                  extra="测试连接查看工具原始名称，输入名称后按 Enter 添加。空白名单不允许执行任何工具。"
                >
                  <Select mode="tags" placeholder="输入工具名称" />
                </Form.Item>
              </>
            )}
            {editor.kind === "skills" && (
              <>
                <Form.Item name="description" label="简介">
                  <Input />
                </Form.Item>
                <Form.Item
                  name="content"
                  label="SKILL.md"
                  rules={[{ required: true }]}
                  extra="当前版本加载任务说明；脚本执行请通过已授权的 MCP 工具提供。"
                >
                  <Input.TextArea rows={18} className="code-input" />
                </Form.Item>
              </>
            )}
            {editor.kind === "workflows" && (
              <>
                <Form.Item name="description" label="简介">
                  <Input />
                </Form.Item>
                <Alert
                  type="info"
                  message="模板变量：{{input}}、{{previous}}、{{steps.步骤ID}}"
                  description="从第一步开始执行。next 为成功后的步骤，条件步骤使用 contains 和 otherwise。没有 next 时结束；禁止循环。"
                />
                <Form.Item
                  name="steps_json"
                  label="步骤配置（JSON 数组）"
                  rules={[{ required: true }]}
                >
                  <Input.TextArea rows={22} className="code-input" />
                </Form.Item>
              </>
            )}
            {editor.kind !== "workflows" && (
              <Form.Item name="enabled" label="启用" valuePropName="checked">
                <Switch />
              </Form.Item>
            )}
          </Form>
        )}
      </Drawer>
      <Modal
        title={"运行：" + (workflowRun?.name || "")}
        open={!!workflowRun}
        onCancel={() => {
          if (!workflowBusy) setWorkflowRun(null);
        }}
        onOk={() => void executeWorkflow()}
        confirmLoading={workflowBusy}
        okText="开始执行"
        cancelButtonProps={{ disabled: workflowBusy }}
        closable={!workflowBusy}
        maskClosable={!workflowBusy}
      >
        <p>选择默认模型并输入任务。执行期间请保持页面打开。</p>
        <Select
          style={{ width: "100%", marginBottom: 16 }}
          value={model}
          onChange={setModel}
          options={models
            .filter((m) => m.enabled)
            .map((m) => ({ value: m.id, label: m.name }))}
        />
        <Input.TextArea
          rows={6}
          value={workflowInput}
          onChange={(e) => setWorkflowInput(e.target.value)}
          placeholder="输入流程需要处理的内容"
        />
      </Modal>
      <Drawer
        title={detail?.name || "执行详情"}
        width={720}
        open={!!detail}
        onClose={() => setDetail(null)}
      >
        {detail?.markdown ? (
          <Markdown text={detail.content} />
        ) : (
          detail && (
            <>
              {detail.status && statusTag(detail.status)}
              {detail.error && <Alert type="error" message={detail.error} />}
              {detail.output && (
                <div className="detail-output">
                  <Markdown text={detail.output} />
                </div>
              )}
              {detail.tools && (
                <Alert
                  type="success"
                  message={`发现 ${detail.tools.length} 个工具`}
                  description="复制工具 name 到连接的白名单中，保存并启用后即可使用。"
                />
              )}
              <pre className="json-detail">
                {JSON.stringify(detail, null, 2)}
              </pre>
            </>
          )
        )}
      </Drawer>
    </Layout>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#5269de",
          borderRadius: 10,
          fontFamily: 'Inter, "Segoe UI", "Microsoft YaHei", sans-serif',
        },
        components: {
          Layout: { siderBg: "#fff", bodyBg: "#f6f7fb" },
          Menu: {
            itemSelectedBg: "#eef1ff",
            itemSelectedColor: "#465dcf",
            itemHeight: 46,
          },
        },
      }}
    >
      <AntApp>
        <Console />
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);
