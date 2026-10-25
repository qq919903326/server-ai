import { test, expect } from "@playwright/test";

test("browser folder grant needs no download or device input and reads through real P2P", async ({
  page,
  request,
}) => {
  let project = "",
    device = "";
  const workflow = "browser-folder-" + Date.now();
  // Headless cannot click an OS directory picker. Use genuine browser filesystem
  // handles from OPFS for the selected folder; permissions/storage/WebRTC remain real.
  await page.addInitScript(() => {
    Object.defineProperty(window, "showDirectoryPicker", {
      configurable: true,
      value: async () => {
        const root = await navigator.storage.getDirectory();
        const folder = await root.getDirectoryHandle(
          "browser-granted-project",
          { create: true },
        );
        const handle = await folder.getFileHandle("README.txt", {
          create: true,
        });
        const writer = await handle.createWritable();
        await writer.write("browser project via WebRTC");
        await writer.close();
        return folder;
      },
    });
  });
  try {
    await page.goto("/");
    await page.getByRole("menuitem", { name: "本地文件库" }).click();
    await expect(
      page.getByRole("button", { name: "下载本地助手" }),
    ).toHaveCount(0);
    await expect(page.getByLabel("本地设备 ID")).toHaveCount(0);
    await expect(page.locator("main input:not([type=checkbox])")).toHaveCount(0);
    await page
      .getByRole("button", { name: "添加本地文件库", exact: true })
      .click();
    const card = page.locator(".resource-card").filter({
      has: page.getByRole("heading", {
        name: "browser-granted-project",
        exact: true,
      }),
    });
    await expect(card).toBeVisible({ timeout: 15000 });
    const records = await (await request.get("/api/projects")).json();
    const saved = records.find(
      (p: any) => p.name === "browser-granted-project",
    );
    project = saved.id;
    device = saved.device_id;
    await expect(card.getByRole("button", { name: "用于对话" })).toBeEnabled({
      timeout: 40000,
    });
    const other = await page.context().newPage();
    await other.goto("/");
    await other.getByRole("menuitem", { name: "本地文件库" }).click();
    await expect(
      other.getByText("由其他标签页保持连接", { exact: true }),
    ).toBeVisible({ timeout: 15000 });
    expect(
      await other.evaluate(
        () =>
          performance
            .getEntriesByType("resource")
            .filter((e) => e.name.includes("/agent/signal/")).length,
      ),
    ).toBe(0);
    await other.close();
    const created = await request.put("/api/workflows/" + workflow, {
      data: {
        name: workflow,
        steps: [
          {
            id: "read",
            type: "tool",
            server_id: "project-" + project,
            tool_name: "read_text",
            arguments: { path: "README.txt" },
          },
        ],
      },
    });
    expect(created.ok()).toBeTruthy();
    const read = await (
      await request.post("/api/workflows/" + workflow + "/run", {
        data: { input: "" },
      })
    ).json();
    expect(read.status).toBe("completed");
    expect(read.output).toContain("browser project via WebRTC");
    await page.getByRole("menuitem", { name: "对话工作台" }).click();
    const again = await (
      await request.post("/api/workflows/" + workflow + "/run", {
        data: { input: "" },
      })
    ).json();
    expect(again.status).toBe("completed");
    await page.reload();
    await page.getByRole("menuitem", { name: "本地文件库" }).click();
    await expect(card.getByRole("button", { name: "用于对话" })).toBeEnabled({
      timeout: 40000,
    });
    await card.getByRole("button", { name: "移除文件库", exact: true }).click();
    await page.getByRole("button", { name: "确 定", exact: true }).click();
    await expect(card).toHaveCount(0);
  } finally {
    if (project) await request.delete("/api/projects/" + project);
    if (device) await request.delete("/api/projects/assistants/" + device);
    await request.delete("/api/workflows/" + workflow);
    const runs = await (await request.get("/api/runs")).json();
    for (const run of runs.filter((r: any) => r.name === workflow))
      await request.delete("/api/runs/" + run.id);
  }
});

test("cancelling folder picker does not create a project or pairing", async ({
  page,
  request,
}) => {
  const before = await (await request.get("/api/projects")).json();
  await page.addInitScript(() =>
    Object.defineProperty(window, "showDirectoryPicker", {
      value: async () => {
        throw new DOMException("cancelled", "AbortError");
      },
    }),
  );
  await page.goto("/");
  await page.getByRole("menuitem", { name: "本地文件库" }).click();
  await page
    .getByRole("button", { name: "添加本地文件库", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "添加本地文件库", exact: true }),
  ).toBeEnabled();
  expect(await (await request.get("/api/projects")).json()).toEqual(before);
});

test("denied directory permission does not pair or create a project", async ({
  page,
  request,
}) => {
  const before = await (await request.get("/api/projects")).json();
  const devices = await (
    await request.get("/api/projects/assistant-status")
  ).json();
  await page.addInitScript(() =>
    Object.defineProperty(window, "showDirectoryPicker", {
      value: async () => ({
        name: "denied",
        queryPermission: async () => "denied",
      }),
    }),
  );
  await page.goto("/");
  await page.getByRole("menuitem", { name: "本地文件库" }).click();
  await page
    .getByRole("button", { name: "添加本地文件库", exact: true })
    .click();
  await expect(
    page.getByText("文件库目录未获得读取授权", { exact: true }),
  ).toBeVisible();
  expect(await (await request.get("/api/projects")).json()).toEqual(before);
  expect(
    await (await request.get("/api/projects/assistant-status")).json(),
  ).toEqual(devices);
});
