import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { downloadAssistant } from "../api";

const mime = "application/vnd.microsoft.portable-executable";
let fetchMock: ReturnType<typeof vi.fn>;
let link: {
  href: string;
  download: string;
  click: ReturnType<typeof vi.fn>;
  remove: ReturnType<typeof vi.fn>;
};
let revoke: ReturnType<typeof vi.fn>;
beforeEach(() => {
  vi.useFakeTimers();
  fetchMock = vi.fn();
  link = { href: "", download: "", click: vi.fn(), remove: vi.fn() };
  revoke = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("sessionStorage", { getItem: () => "test-app-token" });
  vi.stubGlobal("document", {
    createElement: () => link,
    body: { appendChild: vi.fn() },
  });
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn(() => "blob:exe"),
    revokeObjectURL: revoke,
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("POSTs without body or URL token, saves the EXE, and releases its object URL", async () => {
  fetchMock.mockResolvedValue(
    new Response("MZ-test", { headers: { "Content-Type": mime } }),
  );
  await downloadAssistant();
  const [url, request] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/projects/assistant-download");
  expect(request.method).toBe("POST");
  expect(request.body).toBeUndefined();
  expect(request.headers.Authorization).toBe("Bearer test-app-token");
  expect(request.signal).toBeInstanceOf(AbortSignal);
  expect(link.download).toBe("AIStudioAssistant.exe");
  expect(link.click).toHaveBeenCalledOnce();
  expect(link.remove).toHaveBeenCalledOnce();
  await vi.runAllTimersAsync();
  expect(revoke).toHaveBeenCalledWith("blob:exe");
});

it.each([401, 503])(
  "shows JSON detail for HTTP %s and never saves it as EXE",
  async (status) => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "助手模板不可用 / 请重新登录" }), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(downloadAssistant()).rejects.toThrow(
      "助手模板不可用 / 请重新登录",
    );
    expect(link.click).not.toHaveBeenCalled();
  },
);

it("rejects unexpected 200 JSON, non-JSON failures and empty EXEs", async () => {
  for (const response of [
    new Response("{}", { headers: { "Content-Type": "application/json" } }),
    new Response("<html>gateway error</html>", { status: 502 }),
    new Response("", { headers: { "Content-Type": mime } }),
  ]) {
    fetchMock.mockResolvedValue(response);
    await expect(downloadAssistant()).rejects.toThrow(/下载/);
  }
  expect(link.click).not.toHaveBeenCalled();
});

it("network and timeout failures allow a later retry", async () => {
  fetchMock
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockRejectedValueOnce(new DOMException("timeout", "TimeoutError"))
    .mockResolvedValueOnce(
      new Response("MZ", { headers: { "Content-Type": mime } }),
    );
  await expect(downloadAssistant()).rejects.toThrow("网络连接失败");
  await expect(downloadAssistant()).rejects.toThrow("超时");
  await downloadAssistant();
  expect(link.click).toHaveBeenCalledOnce();
});

it("releases the URL even if starting the download fails", async () => {
  fetchMock.mockResolvedValue(
    new Response("MZ", { headers: { "Content-Type": mime } }),
  );
  link.click.mockImplementation(() => {
    throw new Error("save blocked");
  });
  await expect(downloadAssistant()).rejects.toThrow("save blocked");
  await vi.runAllTimersAsync();
  expect(revoke).toHaveBeenCalledWith("blob:exe");
});
