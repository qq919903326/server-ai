export function headers() {
  const token = sessionStorage.getItem("ai-token");
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}
export async function api(path: string, method = "GET", body?: unknown) {
  const response = await fetch("/api" + path, {
    method,
    headers: headers(),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail || data),
    );
  return data;
}

export async function downloadAssistant() {
  try {
    const response = await fetch("/api/projects/assistant-download", {
      method: "POST",
      headers: headers(),
      signal: AbortSignal.timeout(120000),
    });
    const type = response.headers.get("Content-Type") || "";
    if (
      response.status !== 200 ||
      !type
        .toLowerCase()
        .includes("application/vnd.microsoft.portable-executable")
    ) {
      let detail = "";
      if (type.toLowerCase().includes("json")) {
        const data = await response.json().catch(() => null);
        if (typeof data?.detail === "string") detail = data.detail;
      }
      throw new Error(
        detail ||
          (response.status === 503
            ? "Windows 授权助手暂不可下载，请稍后重试或联系管理员配置助手模板"
            : `助手下载失败（HTTP ${response.status}），请稍后重试`),
      );
    }
    const blob = await response.blob();
    if (!blob.size) throw new Error("助手下载内容为空，请重试");
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    try {
      link.href = url;
      link.download = "AIStudioAssistant.exe";
      document.body.appendChild(link);
      link.click();
    } finally {
      link.remove();
      // Give the browser time to start saving before releasing the backing blob.
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  } catch (error) {
    if (error instanceof TypeError)
      throw new Error("网络连接失败，无法下载助手，请检查网络后重试");
    if (
      error instanceof Error &&
      ["TimeoutError", "AbortError"].includes(error.name)
    )
      throw new Error("助手下载超时，请重试");
    throw error;
  }
}
export async function streamEvents(
  path: string,
  body: unknown,
  signal: AbortSignal,
  onEvent: (type: string, data: any) => void,
) {
  const response = await fetch("/api" + path, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "请求失败");
  }
  if (!response.body) throw new Error("浏览器不支持流式响应");
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let pending = "";
  while (true) {
    const { value, done } = await reader.read();
    pending += decoder.decode(value, { stream: !done });
    let index;
    while ((index = pending.indexOf("\n\n")) !== -1) {
      const block = pending.slice(0, index);
      pending = pending.slice(index + 2);
      const event = block.match(/^event: (.+)$/m)?.[1];
      const data = block.match(/^data: (.+)$/m)?.[1];
      if (event && data) onEvent(event, JSON.parse(data));
    }
    if (done) break;
  }
}

export function streamChat(
  body: unknown,
  signal: AbortSignal,
  onEvent: (type: string, data: any) => void,
) {
  return streamEvents("/chat", body, signal, onEvent);
}
