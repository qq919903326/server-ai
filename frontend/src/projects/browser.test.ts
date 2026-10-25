import { it, expect, vi } from "vitest";
const shared = vi.hoisted(() => ({ saved: undefined as any }));
vi.mock("./storage", () => ({
  browserStore: async (operation: string, _key: string, value: any) => {
    if (operation === "put")
      shared.saved = { ...value, handles: { ...value.handles } };
    return shared.saved
      ? { ...shared.saved, handles: { ...shared.saved.handles } }
      : undefined;
  },
}));
vi.mock("../api", () => ({
  api: async (path: string, _method: string, body: any) =>
    path.endsWith("browser-session")
      ? body
      : { id: "new-project", name: "new" },
}));
import { BrowserProjects } from "./browser";

it("a stale poll cannot delete a newly granted directory or its saved handle", async () => {
  const handle: any = { name: "new", queryPermission: async () => "granted" };
  vi.stubGlobal("window", {
    isSecureContext: true,
    showDirectoryPicker: async () => handle,
  });
  const browser = new BrowserProjects();
  shared.saved = {
    session: { device_id: "web-test", token: "secret", ice_servers: [] },
    handles: {},
  };
  browser.withState = async (action) => action();
  browser.acquireLeader = async () => true;
  let started!: () => void, release!: (value: any) => void;
  const began = new Promise<void>((resolve) => (started = resolve));
  const snapshot = new Promise((resolve) => (release = resolve));
  browser.agent = async (path) => {
    if (path === "projects") {
      started();
      return snapshot;
    }
    return {};
  };
  const poll = browser.tick();
  await began;
  await browser.add();
  expect(browser.handles["new-project"]).toBe(handle);
  release({ projects: [], ice_servers: [] });
  await poll;
  expect(browser.handles["new-project"]).toBe(handle);
  expect(shared.saved.handles["new-project"]).toBe(handle);
  vi.unstubAllGlobals();
});
