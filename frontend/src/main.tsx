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
import { ModelFields } from "./models/ModelFields";
import { Projects } from "./Projects";
import { browserProjects } from "./projects/browser";
import {
  saveLocalSession,
  saveLocalRun,
  localRecords,
  localSession,
  activeLocalSession,
  clearActiveLocalSession,
  mergeRecords,
  migrateServerHistory,
} from "./chat/history";
import { modelOptions } from "./models/options";
import { api, streamChat } from "./api";
import { uuid } from "./utils/id";
import "./style.css";
import "./responsive.css";
import { WorkflowEditor } from "./workflow/WorkflowEditor";
import { WorkflowRunView } from "./workflow/WorkflowRunView";
import { WorkflowPreview } from "./workflow/WorkflowPreview";
import type { Workflow, Run } from "./workflow/graph";

type RecordData = { [key: string]: any };
const titles: Record<string, [string, string]> = {
  projects: ["本地文件库", "下载授权助手，在本机选择文件夹，网页自动连接"],
  chat: ["对话工作台", "让模型、技能与工具协同完成任务"],
  models: ["模型连接", "统一管理 DeepSeek、GPT、MiMo 和兼容服务"],
  mcp: ["MCP 工具", "连接外部能力，明确每项工具的执行权限"],
  skills: ["Skills 技能", "将可复用的任务方法，变成助手的能力"],
  workflows: ["Workflow 工作流", "用明确的步骤，组织可重复执行的任务"],
  runs: ["运行记录", "追踪每一次执行的结果与工具调用"],
};
const ids = () => uuid().replaceAll("-", "").slice(0, 12);
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
  const [storeSessions, setStoreSessions] = useState(false),
    [storageKnown, setStorageKnown] = useState(false),
    [storageBusy, setStorageBusy] = useState(false);
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
  const [workflowRun, setWorkflowRun] = useState<RecordData | null>(null);
  const [visualWorkflow, setVisualWorkflow] = useState<Workflow | null>(null);
  const [runHistory, setRunHistory] = useState<Run | null>(null);
  const controller = useRef<AbortController | null>(null),
    bottom = useRef<HTMLDivElement>(null);

  async function refresh() {
    setLoading(true);
    try {
      const storage = await api("/settings/session-storage");
      setStoreSessions(storage.enabled);
      setStorageKnown(true);
      if (!storage.enabled) await migrateServerHistory();
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
      setRuns(mergeRecords(data[4], await localRecords("run")));
      setSessions(mergeRecords(data[5], await localRecords("session")));
    } catch (e) {
      setConnected(false);
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void refresh();
    let cancelled = false;
    void activeLocalSession()
      .then((saved) => {
        if (saved && !cancelled) {
          setSid(saved.id);
          setMessages(saved.messages || []);
          setTrace(saved.trace || []);
        }
      })
      .catch((error) => message.error("无法读取本地会话：" + error.message));
    browserProjects.start();
    return () => {
      cancelled = true;
      browserProjects.stop();
    };
  }, []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, status]);
  useEffect(() => {
    if (!sid || !messages.length) return;
    const row = {
      id: sid,
      title:
        messages.find((m) => m.role === "user")?.content?.slice(0, 40) ||
        "本地会话",
      messages,
      trace,
    };
    void saveLocalSession(row)
      .then(() => localRecords("session"))
      .then((rows) => setSessions((previous) => mergeRecords(previous, rows)))
      .catch((error) => message.error("本地会话保存失败：" + error.message));
  }, [sid, messages, trace]);
  async function changeSessionStorage(enabled: boolean) {
    setStorageBusy(true);
    try {
      await api("/settings/session-storage", "PUT", { enabled });
      setStoreSessions(enabled);
      await refresh();
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setStorageBusy(false);
    }
  }
  const selectableModels = modelOptions(models);
  const fileLibraryIds = new Set(
    mcps.filter((m) => m.service_type === "project").map((m) => m.id),
  );
  const configured = models.filter((m) => m.has_key).length;

  function edit(kind: string, data?: RecordData) {
    if (kind === "mcp" && data?.service_type === "file_p2p") {
      setPage("projects");
      return;
    }
    if (kind === "workflows") {
      setVisualWorkflow(
        (data || {
          id: ids(),
          name: "新工作流",
          description: "",
          steps: [
            {
              id: "first",
              label: "开始处理",
              type: "prompt",
              prompt: "请处理以下任务：\n{{input}}",
            },
          ],
        }) as Workflow,
      );
      return;
    }
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
    };
    const value = { ...(data || defaults[kind]) };
    if (kind === "models") value.api_key = "";
    if (kind === "mcp") value.password = "";
    setEditor({ kind, data: value });
    form.resetFields();
    form.setFieldsValue(value);
  }
  function connectDatabase(engine: "mysql" | "postgres") {
    edit("mcp", {
      id: engine + "-" + ids(),
      name: engine === "mysql" ? "MySQL" : "PostgreSQL",
      transport: "http",
      service_type: "database",
      database_engine: engine,
      url: "",
      username: "",
      password: "",
      allowed_tools: ["list_tables", "describe_table", "query"],
      enabled: true,
    });
  }
  function connectFiles() {
    setPage("projects");
  }
  function connectSSH() {
    edit("mcp", {
      id: "ssh-" + ids(), name: "SSH 服务器", service_type: "ssh",
      host: "", port: 22, username: "", password: "",
      allowed_tools: ["exec", "list_directory", "read_file", "write_file"],
      enabled: true,
    });
  }
  function connectJenkins() {
    edit("mcp", {
      id: "jenkins-" + ids(), name: "Jenkins", service_type: "jenkins",
      url: "", username: "", password: "",
      allowed_tools: ["server_info", "list_jobs", "job_info", "build_info", "build_log",
        "queue_info", "trigger_build", "stop_build", "set_job_enabled",
        "get_job_config", "update_job_config", "create_job"],
      enabled: true,
    });
  }
  async function save() {
    if (!editor) return;
    try {
      const data = await form.validateFields();
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
    if (!storageKnown) {
      message.error("请先连接服务器并确认会话存储设置");
      return;
    }
    let activeId: string, runId: string;
    try {
      activeId = sid || ids();
      runId = ids();
    } catch (error) {
      message.error((error as Error).message);
      return;
    }
    const startedAt = new Date().toISOString();
    let finishedRun: RecordData | undefined,
      runStatus = "completed";
    setSid(activeId);
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
          session_id: activeId,
          history: messages
            .filter((m) => m.role === "user" || m.role === "assistant")
            .map((m) => ({ role: m.role, content: m.content })),
          store_session: storeSessions,
          skill_ids: selectedSkills,
          mcp_ids: selectedMCP,
        },
        controller.current.signal,
        (event, data) => {
          if (event === "session") runId = data.run_id || runId;
          if (event === "done") {
            finishedRun = data.run;
            runStatus = data.status || runStatus;
          }
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
            runStatus = "failed";
            message.error(data.message);
            setMessages((prev) => [
              ...prev,
              { role: "error", content: data.message },
            ]);
          }
        },
      );
    } catch (e) {
      runStatus = (e as Error).name === "AbortError" ? "cancelled" : "failed";
      if ((e as Error).name !== "AbortError")
        message.error((e as Error).message);
    } finally {
      try {
        await saveLocalRun(
          (finishedRun || {
            id: runId,
            kind: "chat",
            name: value.slice(0, 40),
            session_id: activeId,
            model_id: model,
            started_at: startedAt,
            finished_at: new Date().toISOString(),
            status: runStatus,
          }) as any,
        );
      } catch (error) {
        message.error(
          "聊天运行记录保存到本地失败：" + (error as Error).message,
        );
      }
      setBusy(false);
      setStatus("");
      controller.current = null;
      void refresh();
    }
  }
  async function openSession(item: RecordData) {
    if (busy) return;
    try {
      const data =
        (await localSession(item.id)) || (await api("/sessions/" + item.id));
      setSid(item.id);
      setMessages(data.messages);
      setTrace(data.trace || []);
    } catch (e) {
      message.error((e as Error).message);
    }
  }
  const actions = (kind: string, item: RecordData) => (
    <Space>
      <Tooltip title="编辑">
        <Button icon={<EditOutlined />} onClick={() => edit(kind, item)}>
          {kind === "workflows" ? "图形编辑" : null}
        </Button>
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
            {
              key: "projects",
              icon: <FileTextOutlined />,
              label: "本地文件库",
            },
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
            {page !== "chat" && page !== "runs" && page !== "projects" && (
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
                      options={selectableModels
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
                      void clearActiveLocalSession();
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
                <Space style={{ marginBottom: 8 }}>
                  <label>服务端存储会话</label>
                  <Switch
                    aria-label="服务端存储会话"
                    checked={storeSessions}
                    disabled={busy || !storageKnown}
                    loading={storageBusy}
                    onChange={(enabled) => void changeSessionStorage(enabled)}
                  />
                </Space>
                <p className="helper">
                  {!storageKnown
                    ? "正在读取存储设置…"
                    : storeSessions
                      ? "已开启服务端副本；浏览器仍保留本地历史。"
                      : "已关闭：会话与聊天运行记录只保存在当前浏览器。"}
                </p>
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
                <label>本地文件库</label>
                <Select
                  aria-label="本地文件库"
                  mode="multiple"
                  placeholder="可选择多个本地文件库"
                  notFoundContent="请先在本地文件库中添加并授权目录"
                  value={selectedMCP.filter((id) => fileLibraryIds.has(id))}
                  onChange={(ids) =>
                    setSelectedMCP((prev) => [
                      ...prev.filter((id) => !fileLibraryIds.has(id)),
                      ...ids,
                    ])
                  }
                  options={mcps
                    .filter((m) => fileLibraryIds.has(m.id))
                    .map((m) => ({
                      value: m.id,
                      label: m.name.replace(/^(项目|文件库) · /, ""),
                      disabled: !m.enabled,
                    }))}
                />
                <Button
                  type="link"
                  size="small"
                  onClick={() => setPage("projects")}
                >
                  管理本地文件库
                </Button>
                <label>MCP 工具服务</label>
                <Select
                  aria-label="MCP 工具服务"
                  mode="multiple"
                  placeholder={
                    mcps.length ? "选择工具服务" : "请先配置数据库或其他 MCP"
                  }
                  notFoundContent="尚未配置远程 MCP 服务"
                  value={selectedMCP.filter((id) => !fileLibraryIds.has(id))}
                  onChange={(ids) =>
                    setSelectedMCP((prev) => [
                      ...prev.filter((id) => fileLibraryIds.has(id)),
                      ...ids,
                    ])
                  }
                  options={mcps
                    .filter((m) => !fileLibraryIds.has(m.id))
                    .map((m) => ({
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
                    <p className="helper">开始对话后会自动保存到本地浏览器</p>
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
              {page === "projects" && (
                <Projects
                  onChanged={() => void refresh()}
                  onUse={(ids) => {
                    setSelectedMCP((prev) =>
                      Array.from(new Set([...prev, ...ids])),
                    );
                    setPage("chat");
                    void refresh();
                  }}
                />
              )}
              {page === "mcp" && (
                <>
                  <Alert
                    className="section-alert"
                    type="info"
                    showIcon
                    message="每个连接独立授权工具"
                    description="Jenkins 填写地址或 IP:端口、账号和密码；SSH 填写 IP、端口、账号和密码；MySQL / PostgreSQL 填写数据库 URL、账号和密码。其他 MCP 服务填写远程 HTTP 地址。"
                  />
                  <div className="section-tools">
                    <span>
                      Jenkins、SSH 和数据库由服务器直接连接，保存后即可在对话和工作流中使用。本地文件需先运行代理并授权目录。
                    </span>
                    <Space wrap>
                      <Button onClick={() => connectDatabase("mysql")}>
                        连接 MySQL
                      </Button>
                      <Button onClick={() => connectDatabase("postgres")}>
                        连接 PostgreSQL
                      </Button>
                      <Button onClick={connectFiles}>本地文件库</Button>
                      <Button onClick={connectSSH}>连接 SSH</Button>
                      <Button onClick={connectJenkins}>连接 Jenkins</Button>
                    </Space>
                  </div>
                  {!mcps.length && (
                    <Card>
                      <Empty description="尚未配置数据库或 MCP 服务">
                        <Button type="primary" onClick={() => edit("mcp")}>
                          添加远程 MCP
                        </Button>
                      </Empty>
                    </Card>
                  )}
                  <div className="card-grid">
                    {mcps
                      .filter((item) => item.service_type !== "project")
                      .map((item) => (
                        <Card key={item.id} className="resource-card">
                          <div className="resource-heading">
                            <div className="provider-icon mcp">
                              <ApiOutlined />
                            </div>
                            <div>
                              <h3>{item.name}</h3>
                              <span>{item.service_type === "jenkins" ? "JENKINS · 直连" : item.service_type === "ssh" ? "SSH · 直连" : item.service_type === "database" ? `${item.database_engine === "mysql" ? "MYSQL" : "POSTGRESQL"} · 直连` : "REMOTE HTTP"}</span>
                            </div>
                            <Tag color={item.enabled ? "green" : "default"}>
                              {item.enabled ? "已启用" : "未启用"}
                            </Tag>
                          </div>
                          <div className="endpoint tall">{item.service_type === "ssh" ? `${item.username}@${item.host.includes(":") ? `[${item.host}]` : item.host}:${item.port}` : item.url}</div>
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
                    message="可视化工作流"
                    description="拖动节点、连接端口，配置模型和工具的调用关系。条件节点支持两条分支，运行时实时显示实际执行路径。"
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
                        <WorkflowPreview workflow={item as Workflow} />
                        <div className="card-actions">
                          <Button
                            type="primary"
                            ghost
                            onClick={() => {
                              setWorkflowRun(item);
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
                          <Button
                            onClick={() =>
                              r.kind === "workflow" && r.workflow_snapshot
                                ? setRunHistory(r as Run)
                                : setDetail(r)
                            }
                          >
                            查看详情
                          </Button>
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
              <ModelFields key={editor.data.id} config={editor.data} />
            )}
            {editor.kind === "mcp" && (
              <>
                {editor.data.service_type === "jenkins" ? (
                  <>
                    <Alert type="info" showIcon message="填写 Jenkins 连接信息"
                      description="保存后在对话或工作流中选择此连接，Agent 即可查看任务、触发参数化构建、读取日志、停止构建、启停任务和管理任务配置。操作权限与 Jenkins 账号一致。"
                      style={{ marginBottom: 20 }} />
                    <Form.Item name="url" label="Jenkins 地址"
                      rules={[{ required: true, whitespace: true, message: "请输入 Jenkins 地址或 IP:端口" }]}
                      extra="支持 http(s)://地址、IP:端口，以及 /jenkins 等访问路径；省略协议时使用 HTTP。">
                      <Input placeholder="192.168.1.10:8080 或 https://ci.example.com/jenkins" autoComplete="off" />
                    </Form.Item>
                    <Form.Item name="username" label="账号" rules={[{ required: true, whitespace: true, message: "请输入 Jenkins 账号" }]}>
                      <Input autoComplete="off" />
                    </Form.Item>
                    <Form.Item name="password" label="密码 / API Token"
                      rules={editor.data.has_password ? [] : [{ required: true, message: "请输入 Jenkins 密码或 API Token" }]}
                      extra={editor.data.has_password ? "已保存凭据，留空保持原凭据。" : "填写 Jenkins 登录密码；也支持此账号的 API Token。"}>
                      <Input.Password autoComplete="new-password" placeholder={editor.data.has_password ? "已保存，留空不修改" : "Jenkins 密码或 API Token"} />
                    </Form.Item>
                    <Form.Item name="allowed_tools" label="工具白名单"
                      extra="默认授权 Jenkins 操作工具，可移除不需要的写操作。空白名单不允许执行任何工具。">
                      <Select mode="tags" />
                    </Form.Item>
                  </>
                ) : editor.data.service_type === "ssh" ? (
                  <>
                    <Alert type="info" showIcon message="填写 SSH 连接信息"
                      description="保存后在对话中选择此连接，Agent 即可执行远程命令、浏览目录、读取和写入文件，操作权限与登录账号一致。"
                      style={{ marginBottom: 20 }} />
                    <Form.Item name="host" label="IP / 主机名" rules={[{ required: true, whitespace: true, message: "请输入服务器 IP 或主机名" }]}>
                      <Input placeholder="192.168.1.10" autoComplete="off" />
                    </Form.Item>
                    <Form.Item name="port" label="SSH 端口" rules={[{ required: true, message: "请输入 SSH 端口" }]}>
                      <Input type="number" min={1} max={65535} />
                    </Form.Item>
                    <Form.Item name="username" label="账号" rules={[{ required: true, whitespace: true, message: "请输入 SSH 账号" }]}>
                      <Input autoComplete="off" />
                    </Form.Item>
                    <Form.Item name="password" label="密码" rules={editor.data.has_password ? [] : [{ required: true, message: "请输入 SSH 密码" }]}
                      extra={editor.data.has_password ? "已保存密码，留空保持原密码。" : "填写服务器 SSH 登录密码。"}>
                      <Input.Password autoComplete="new-password" placeholder={editor.data.has_password ? "已保存，留空不修改" : "SSH 登录密码"} />
                    </Form.Item>
                  </>
                ) : editor.data.service_type === "database" ? (
                  <>
                    <Alert type="info" showIcon message="填写数据库连接信息"
                      description="保存后即可使用列出表、查看字段和只读查询工具。数据库地址需要能从 AI 服务器访问。"
                      style={{ marginBottom: 20 }} />
                    <Form.Item name="url" label="数据库 URL"
                      rules={[{ required: true, message: "请输入数据库 URL" }]}
                      extra="包含主机、端口和数据库名；也支持 主机:端口/数据库名。">
                      <Input placeholder={editor.data.database_engine === "mysql" ? "mysql://127.0.0.1:3306/my_database" : "postgresql://127.0.0.1:5432/my_database"} />
                    </Form.Item>
                    <Form.Item name="username" label="账号" rules={[{ required: true, whitespace: true, message: "请输入数据库账号" }]}>
                      <Input autoComplete="off" />
                    </Form.Item>
                    <Form.Item name="password" label="密码" extra={editor.data.has_password ? "已保存密码，留空保持原密码。" : "填写数据库密码；无密码时可留空。"}>
                      <Input.Password autoComplete="new-password" placeholder={editor.data.has_password ? "已保存，留空不修改" : "数据库密码"} />
                    </Form.Item>
                    {editor.data.has_password && <Form.Item name="clear_password" label="清除已保存密码" valuePropName="checked"><Switch /></Form.Item>}
                  </>
                ) : (
                  <>
                <Alert
                  type="info"
                  showIcon
                  message="填写 MCP 服务地址"
                  description="这里连接已部署的远程 MCP 服务。MySQL / PostgreSQL 请使用管理页的数据库连接按钮。保存并启用后可在聊天右侧选择。"
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
            <Form.Item name="enabled" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Form>
        )}
      </Drawer>
      {visualWorkflow && (
        <WorkflowEditor
          workflow={visualWorkflow}
          models={selectableModels}
          skills={skills}
          mcps={mcps}
          onClose={() => setVisualWorkflow(null)}
          onSaved={() => void refresh()}
        />
      )}
      {workflowRun && (
        <WorkflowRunView
          workflow={workflowRun as Workflow}
          models={selectableModels}
          defaultModel={model}
          onClose={() => setWorkflowRun(null)}
          onFinished={() => void refresh()}
        />
      )}
      {runHistory?.workflow_snapshot && (
        <WorkflowRunView
          record={runHistory}
          workflow={runHistory.workflow_snapshot}
          models={selectableModels}
          defaultModel={model}
          onClose={() => setRunHistory(null)}
          onFinished={() => {}}
        />
      )}
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
