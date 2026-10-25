import { api } from "../api";
import { Directory, readDirectory } from "./files";
import { browserStore } from "./storage";

type Session = {
  device_id: string;
  token: string;
  ice_servers: RTCIceServer[];
};
type Saved = { session?: Session; handles: Record<string, Directory> };
const MAX = 60000;

export class BrowserProjects {
  session?: Session;
  handles: Record<string, Directory> = {};
  allowed = new Set<string>();
  pc?: RTCPeerConnection;
  channel?: RTCDataChannel;
  timer?: ReturnType<typeof setInterval>;
  generation = 0;
  working = false;
  connecting = false;
  message = "尚未授权文件库目录";
  listeners = new Set<() => void>();
  initialized: Promise<void> = Promise.resolve();
  leader = false;
  releaseLeader?: () => void;

  async withState<T>(action: () => Promise<T>): Promise<T> {
    if (!navigator.locks) throw new Error("当前浏览器不支持安全的项目授权存储");
    return navigator.locks.request("ai-studio-project-storage", action);
  }
  async restore() {
    const saved: Saved = await browserStore("get", "state");
    if (
      this.session?.device_id &&
      saved?.session?.device_id !== this.session.device_id
    ) {
      this.pc?.close();
      this.pc = undefined;
      this.channel = undefined;
    }
    this.session = saved?.session;
    this.handles = saved?.handles || {};
  }
  async acquireLeader(): Promise<boolean> {
    if (this.leader) return true;
    const generation = this.generation;
    return new Promise((resolve) => {
      void navigator.locks
        .request(
          "ai-studio-project-peer",
          { ifAvailable: true },
          async (lock) => {
            if (!lock || generation !== this.generation) {
              resolve(false);
              return;
            }
            this.leader = true;
            resolve(true);
            await new Promise<void>((release) => {
              this.releaseLeader = release;
            });
            this.leader = false;
            this.releaseLeader = undefined;
          },
        )
        .catch(() => resolve(false));
    });
  }

  notify(value: string) {
    this.message = value;
    this.listeners.forEach((listener) => listener());
  }
  async save() {
    await browserStore("put", "state", {
      session: this.session,
      handles: this.handles,
    });
  }
  start() {
    const generation = ++this.generation;
    this.initialized = (async () => {
      try {
        const saved: Saved = await browserStore("get", "state");
        if (generation !== this.generation) return;
        this.session = saved?.session;
        this.handles = saved?.handles || {};
        if (this.session) {
          this.session = await api(
            "/projects/browser-session",
            "POST",
            this.session,
          );
          if (generation !== this.generation) return;
          await this.tick();
        }
      } catch {
        this.notify("请点击添加本地文件库或重新授权目录");
      }
      if (generation === this.generation)
        this.timer = setInterval(() => void this.tick(), 3000);
    })();
  }
  stop() {
    ++this.generation;
    clearInterval(this.timer);
    this.timer = undefined;
    this.pc?.close();
    this.pc = undefined;
    this.channel = undefined;
    this.releaseLeader?.();
    this.leader = false;
  }
  async agent(path: string, body?: unknown) {
    if (!this.session) throw new Error("浏览器尚未配对");
    const response = await fetch("/agent/" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: "Bearer " + this.session.token,
        "Content-Type": "application/json",
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(45000),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "浏览器连接失败");
    return data;
  }
  async add(projectId?: string) {
    const picker = (window as any).showDirectoryPicker;
    if (!window.isSecureContext || !picker)
      throw new Error(
        "当前浏览器不支持目录授权，请使用支持此功能的浏览器，通过 HTTPS 或 localhost 打开。",
      );
    // Must happen directly in the click handler, before any network or storage await.
    const handle: Directory = await picker.call(window, {
      id: "ai-studio-project",
      mode: "read",
    });
    if ((await handle.queryPermission({ mode: "read" })) !== "granted")
      throw new Error("文件库目录未获得读取授权");
    await this.initialized;
    const project = await this.withState(async () => {
      await this.restore();
      try {
        this.session = await api(
          "/projects/browser-session",
          "POST",
          this.session || {},
        );
      } catch (e) {
        // A deliberate new directory grant may create a fresh pairing after revocation.
        if (!(e instanceof Error) || !e.message.includes("配对已失效")) throw e;
        this.session = await api("/projects/browser-session", "POST", {});
        this.pc?.close();
        this.pc = undefined;
        this.channel = undefined;
      }
      const project = await api("/projects/browser", "POST", {
        name: handle.name,
        device_id: this.session!.device_id,
        project_id: projectId || "",
      });
      this.handles[project.id] = handle;
      await this.save();
      return project;
    });
    await this.tick();
    return project;
  }
  async forget(id: string) {
    this.allowed.delete(id);
    await this.withState(async () => {
      await this.restore();
      delete this.handles[id];
      await this.save();
    });
  }
  async granted(id: string) {
    try {
      return (
        !!this.handles[id] &&
        (await this.handles[id].queryPermission({ mode: "read" })) === "granted"
      );
    } catch {
      return false;
    }
  }
  async tick() {
    if (this.working) return;
    const generation = this.generation;
    this.working = true;
    try {
      await this.withState(() => this.restore());
      if (!this.session || generation !== this.generation) return;
      if (!(await this.acquireLeader())) {
        this.notify("由其他标签页保持连接");
        return;
      }
      const data = await this.agent("projects");
      if (generation !== this.generation) return;
      this.allowed = new Set(data.projects.map((p: any) => p.id));
      // A server snapshot can predate a user's new grant. Never erase local
      // handles from a poll; explicit removal serializes storage via forget().
      const statuses = await Promise.all(
        data.projects.map(async (p: any) => {
          const ready = await this.granted(p.id);
          return {
            id: p.id,
            ready,
            error: ready ? "" : "请在此浏览器重新授权文件库目录",
          };
        }),
      );
      await this.agent("status", { projects: statuses });
      if (generation !== this.generation) return;
      if (!statuses.some((p) => p.ready)) {
        this.notify(
          data.projects.length
            ? "文件库目录需要重新授权"
            : "尚未授权文件库目录",
        );
        return;
      }
      if (
        !this.pc ||
        ["closed", "failed", "disconnected"].includes(this.pc.connectionState)
      ) {
        await this.connect(data.ice_servers || []);
      } else if (this.channel?.readyState === "open")
        this.notify("浏览器已连接");
    } catch (e) {
      if (generation === this.generation) {
        this.pc?.close();
        this.pc = undefined;
        this.channel = undefined;
        this.notify((e as Error).message);
      }
    } finally {
      this.working = false;
    }
  }
  async connect(iceServers: RTCIceServer[]) {
    if (this.connecting || !this.session) return;
    this.connecting = true;
    const generation = this.generation;
    this.pc?.close();
    const pc = new RTCPeerConnection({ iceServers });
    this.pc = pc;
    const channel = pc.createDataChannel("files-v1", { ordered: true });
    this.channel = channel;
    let active = 0;
    const send = (id: string, result: any) => {
      if (channel.readyState !== "open") return;
      let encoded = JSON.stringify({ id, result });
      if (new TextEncoder().encode(encoded).length > MAX)
        encoded = JSON.stringify({
          id,
          result: { error: "响应过大，请减小分页大小" },
        });
      if (channel.bufferedAmount < 256000) channel.send(encoded);
    };
    channel.onmessage = async (event) => {
      if (
        typeof event.data !== "string" ||
        new TextEncoder().encode(event.data).length > MAX
      )
        return;
      let value: any;
      try {
        value = JSON.parse(event.data);
      } catch {
        return;
      }
      if (
        !value ||
        typeof value.id !== "string" ||
        typeof value.method !== "string"
      )
        return;
      if (active >= 8) {
        send(value.id, { error: "浏览器文件请求过多" });
        return;
      }
      active++;
      try {
        if (value.method === "ping") {
          send(value.id, { ok: true });
          return;
        }
        const args = value.arguments;
        if (
          !args ||
          typeof args !== "object" ||
          !this.allowed.has(args.root_id) ||
          !this.handles[args.root_id]
        )
          throw new Error("项目未授权或已移除");
        send(
          value.id,
          await readDirectory(this.handles[args.root_id], value.method, args),
        );
      } catch (e) {
        send(value.id, {
          error:
            e instanceof DOMException
              ? "目录权限已失效或文件无法读取，请重新授权或检查文件"
              : (e as Error).message,
        });
      } finally {
        active--;
      }
    };
    this.notify("正在建立浏览器连接");
    try {
      await pc.setLocalDescription(await pc.createOffer());
      if (pc.iceGatheringState !== "complete")
        await new Promise<void>((resolve, reject) => {
          const timeout = setTimeout(() => {
            pc.removeEventListener("icegatheringstatechange", changed);
            reject(new Error("浏览器网络协商超时"));
          }, 15000);
          const changed = () => {
            if (pc.iceGatheringState === "complete") {
              clearTimeout(timeout);
              pc.removeEventListener("icegatheringstatechange", changed);
              resolve();
            }
          };
          pc.addEventListener("icegatheringstatechange", changed);
          changed();
        });
      if (generation !== this.generation) {
        pc.close();
        return;
      }
      const answer = await this.agent("signal/" + this.session.device_id, {
        type: "offer",
        sdp: pc.localDescription!.sdp,
      });
      if (generation !== this.generation) {
        pc.close();
        return;
      }
      await pc.setRemoteDescription(answer);
      await new Promise<void>((resolve, reject) => {
        if (channel.readyState === "open") {
          resolve();
          return;
        }
        const timeout = setTimeout(() => {
          cleanup();
          reject(new Error("P2P 连接未建立，请检查服务端网络配置"));
        }, 25000);
        const opened = () => {
            cleanup();
            resolve();
          },
          closed = () => {
            cleanup();
            reject(new Error("浏览器连接已断开"));
          };
        const cleanup = () => {
          clearTimeout(timeout);
          channel.removeEventListener("open", opened);
          channel.removeEventListener("close", closed);
        };
        channel.addEventListener("open", opened);
        channel.addEventListener("close", closed);
      });
      if (generation === this.generation) this.notify("浏览器已连接");
    } catch (e) {
      pc.close();
      if (this.pc === pc) {
        this.pc = undefined;
        this.channel = undefined;
      }
      throw e;
    } finally {
      this.connecting = false;
    }
  }
}

export const browserProjects = new BrowserProjects();
