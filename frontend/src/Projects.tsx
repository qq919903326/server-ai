import { useEffect, useRef, useState } from "react";
import {
  App,
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Popconfirm,
  Space,
  Tag,
} from "antd";
import { api, downloadAssistant } from "./api";
import { browserProjects } from "./projects/browser";
import {
  checkProjectPermissions,
  isBrowserProject,
  isProjectReadable,
  projectAvailabilityKey,
  type Project,
} from "./projects/readiness";

type Device = { id: string; kind: "desktop" | "browser"; online: boolean };

export function Projects({
  onUse,
  onChanged,
}: {
  onUse: (ids: string[]) => void;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [projects, setProjects] = useState<Project[]>([]),
    [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceError, setDeviceError] = useState("");
  const [revoking, setRevoking] = useState<string>();
  const availability = useRef("");
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const refreshing = useRef(false);
  const active = useRef(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState(""),
    [connection, setConnection] = useState(browserProjects.message);
  async function refresh() {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      const items = await checkProjectPermissions(
        await api("/projects"),
        (id) => browserProjects.granted(id),
      );
      if (!active.current) return;
      setProjects(items);
      const key = projectAvailabilityKey(items);
      if (key !== availability.current) {
        availability.current = key;
        changed.current();
      }
      setError("");
    } catch (e) {
      if (active.current) setError((e as Error).message);
    } finally {
      refreshing.current = false;
    }
  }
  async function refreshDevices() {
    try {
      const data = await api("/projects/assistant-status");
      if (!active.current) return;
      setDevices(data.devices);
      setDeviceError("");
    } catch (e) {
      if (active.current)
        setDeviceError("无法获取设备状态：" + (e as Error).message);
    }
  }
  useEffect(() => {
    active.current = true;
    void refresh();
    void refreshDevices();
    const timer = setInterval(() => {
      void refresh();
      void refreshDevices();
    }, 3000);
    const listener = () => {
      setConnection(browserProjects.message);
      void refresh();
    };
    browserProjects.listeners.add(listener);
    return () => {
      active.current = false;
      clearInterval(timer);
      browserProjects.listeners.delete(listener);
    };
  }, []);
  async function select(id?: string) {
    setBusy(true);
    try {
      await browserProjects.add(id);
      await refresh();
      onChanged();
      message.success("文件库目录已授权，可以用于对话和工作流");
    } catch (e) {
      if ((e as Error).name !== "AbortError")
        message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const supported =
    window.isSecureContext &&
    typeof (window as any).showDirectoryPicker === "function" &&
    !!navigator.locks;
  const plainHttp = window.location.protocol === "http:";
  const readableSelection = selected.filter((id) =>
    projects.some((project) => project.id === id && isProjectReadable(project)),
  );
  return (
    <>
      {error && (
        <Alert
          type="error"
          showIcon
          message={error}
          style={{ marginBottom: 16 }}
        />
      )}
      <Card style={{ marginBottom: 20 }}>
        <Space wrap style={{ marginBottom: 12 }}>
          <Button
            type="primary"
            loading={downloading}
            onClick={async () => {
              setDownloading(true);
              try {
                await downloadAssistant();
                message.success(
                  "已发起下载，请双击 AIStudioAssistant.exe 并选择文件夹",
                );
              } catch (e) {
                message.error((e as Error).message);
              } finally {
                setDownloading(false);
              }
            }}
          >
            下载 Windows 授权助手
          </Button>
          <Button
            disabled={!supported}
            loading={busy}
            onClick={() => void select()}
          >
            浏览器直接授权
          </Button>
          {(supported || projects.some(isBrowserProject)) && (
            <Tag color={connection === "浏览器已连接" ? "green" : "default"}>
              浏览器：{connection}
            </Tag>
          )}
          <Button
            disabled={!readableSelection.length}
            onClick={() =>
              onUse(readableSelection.map((id) => "project-" + id))
            }
          >
            使用所选文件库
            {readableSelection.length ? `（${readableSelection.length}）` : ""}
          </Button>
        </Space>
        <p className="helper">
          下载 → 双击 → 选择文件夹，网页自动连接。无需安装
          Python、手工填写令牌或路径。 文件通过 P2P
          按需只读访问，不上传目录快照。可勾选多个文件库用于对话和工作流。
        </p>
        {plainHttp && (
          <Alert
            type="warning"
            showIcon
            message="当前 HTTP 页面连接未加密，建议在可信网络使用。优先下载 Windows 授权助手。"
            style={{ marginBottom: 12 }}
          />
        )}
        <Alert
          type={supported ? "info" : "warning"}
          showIcon
          message={
            supported
              ? "仅只读访问你选择的目录，请保持此网页打开"
              : "浏览器直接授权不可用；请下载助手，或通过 HTTPS / localhost 使用支持目录授权的浏览器"
          }
          description="使用浏览器直接授权时，目录授权保存在当前浏览器中；如需再次授权，点击文件库的「重新授权」。使用助手时，请在本机助手中管理授权并保持助手运行。"
        />
      </Card>
      {deviceError && (
        <Alert
          type="warning"
          showIcon
          message={deviceError}
          style={{ marginBottom: 16 }}
        />
      )}
      {!!devices.length && (
        <Card title="授权设备" style={{ marginBottom: 20 }}>
          <Space direction="vertical" style={{ width: "100%" }}>
            {devices.map((device) => (
              <Space wrap key={device.id}>
                <span>
                  {device.kind === "desktop"
                    ? "Windows 授权助手"
                    : "浏览器授权"}{" "}
                  · {device.id}
                </span>
                <Tag color={device.online ? "green" : "default"}>
                  {device.online ? "在线" : "已断线"}
                </Tag>
                <Popconfirm
                  title="撤销此设备的全部目录授权？不会删除本地文件。"
                  onConfirm={async () => {
                    setRevoking(device.id);
                    try {
                      await api(
                        "/projects/assistants/" + encodeURIComponent(device.id),
                        "DELETE",
                      );
                      await Promise.all([refresh(), refreshDevices()]);
                      changed.current();
                      message.success(
                        "设备授权已撤销；如需继续使用，请重新授权",
                      );
                    } catch (e) {
                      message.error((e as Error).message);
                    } finally {
                      setRevoking(undefined);
                    }
                  }}
                >
                  <Button danger loading={revoking === device.id}>
                    撤销设备授权
                  </Button>
                </Popconfirm>
              </Space>
            ))}
          </Space>
        </Card>
      )}
      {!projects.length ? (
        <Empty description="尚未添加本地文件库，请下载助手并选择文件夹，或使用浏览器直接授权" />
      ) : (
        <div className="card-grid">
          {projects.map((project) => (
            <Card key={project.id} className="resource-card">
              <Checkbox
                aria-label={"选择文件库 " + project.name}
                checked={selected.includes(project.id)}
                disabled={!isProjectReadable(project)}
                onChange={(event) =>
                  setSelected((prev) =>
                    event.target.checked
                      ? [...prev, project.id]
                      : prev.filter((id) => id !== project.id),
                  )
                }
              >
                选择文件库
              </Checkbox>
              <h3>{project.name}</h3>
              <Tag color={isProjectReadable(project) ? "green" : "default"}>
                {isProjectReadable(project)
                  ? "可读取"
                  : isBrowserProject(project) && !project.localGranted
                    ? "需要浏览器授权"
                    : !project.online
                      ? "已断线 / 授权已失效"
                      : "目录未就绪"}
              </Tag>
              <Tag>{isBrowserProject(project) ? "浏览器" : "本机助手"}</Tag>
              <p className="helper">本地文件夹：{project.name}</p>
              {!isBrowserProject(project) && (
                <p className="helper">
                  重新授权请在本机助手中选择文件夹；断线时请检查助手是否运行，设备已撤销则需重新授权。
                </p>
              )}
              {project.error && (
                <Alert
                  type="warning"
                  message={project.error}
                  style={{ marginBottom: 12 }}
                />
              )}
              <Space wrap>
                <Button
                  disabled={!isProjectReadable(project)}
                  onClick={() => onUse(["project-" + project.id])}
                >
                  用于对话
                </Button>
                {isBrowserProject(project) && (
                  <Button
                    disabled={!supported}
                    loading={busy}
                    onClick={() => void select(project.id)}
                  >
                    重新授权
                  </Button>
                )}
                <Popconfirm
                  title="移除文件库并停止读取？不会删除本地文件。"
                  onConfirm={async () => {
                    try {
                      await api(
                        "/projects/" + encodeURIComponent(project.id),
                        "DELETE",
                      );
                      if (isBrowserProject(project))
                        await browserProjects.forget(project.id);
                      setSelected((prev) =>
                        prev.filter((id) => id !== project.id),
                      );
                      await refresh();
                      onChanged();
                    } catch (e) {
                      message.error((e as Error).message);
                    }
                  }}
                >
                  <Button danger>移除文件库</Button>
                </Popconfirm>
              </Space>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
