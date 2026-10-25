import { test, expect } from "@playwright/test";
import { createServer } from "node:http";

test("default local history survives reload and resends context without server storage", async ({
  page,
  request,
}) => {
  const calls: any[] = [];
  const provider = createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    calls.push(JSON.parse(raw));
    res.setHeader("Content-Type", "text/event-stream");
    res.end(
      "data: " +
        JSON.stringify({
          choices: [
            {
              index: 0,
              delta: { content: "only local answer" },
              finish_reason: null,
            },
          ],
        }) +
        "\n\ndata: [DONE]\n\n",
    );
  });
  await new Promise<void>((resolve) =>
    provider.listen(0, "127.0.0.1", resolve),
  );
  const port = (provider.address() as { port: number }).port;
  const id = "privacy-model-" + Date.now();
  try {
    await request.put("/api/settings/session-storage", {
      data: { enabled: false },
    });
    await request.put("/api/models/" + id, {
      data: {
        name: "Local Privacy Test",
        provider: "custom",
        base_url: `http://127.0.0.1:${port}/v1`,
        model: "test",
        api_key: "fake-only-key",
      },
    });
    await page.goto("/");
    await expect(
      page.getByRole("switch", { name: "服务端存储会话" }),
    ).not.toBeChecked();
    await expect(
      page.getByRole("switch", { name: "服务端存储会话" }),
    ).toBeEnabled();
    await page.locator(".conversation-toolbar .ant-select").click();
    await page
      .locator(".ant-select-item-option")
      .filter({ hasText: "Local Privacy Test" })
      .click();
    await page
      .getByPlaceholder("输入你的问题或任务…")
      .fill("private browser first turn");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".conversation-panel")).toContainText(
      "only local answer",
    );
    await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
    await page.reload();
    await expect(page.locator(".conversation-panel")).toContainText(
      "private browser first turn",
    );
    await expect(page.locator(".conversation-panel")).toContainText(
      "only local answer",
    );
    await page.locator(".conversation-toolbar .ant-select").click();
    await page
      .locator(".ant-select-item-option")
      .filter({ hasText: "Local Privacy Test" })
      .click();
    await page
      .getByPlaceholder("输入你的问题或任务…")
      .fill("second private turn");
    await page.getByRole("button", { name: "发送" }).click();
    await expect.poll(() => calls.length).toBe(2);
    expect(calls[1].messages.map((m: any) => m.content)).toContain(
      "private browser first turn",
    );
    expect(calls[1].messages.map((m: any) => m.content)).toContain(
      "only local answer",
    );
    expect(await (await request.get("/api/sessions")).json()).toEqual([]);
    const runs = await (await request.get("/api/runs")).json();
    expect(runs.some((r: any) => r.model_id === id)).toBeFalsy();
    const migration = await (
      await request.get("/api/sessions/local-migration")
    ).json();
    expect(migration.sessions).toEqual([]);
    expect(migration.runs).toEqual([]);
  } finally {
    await request.delete("/api/models/" + id);
    await new Promise<void>((resolve) => provider.close(() => resolve()));
  }
});

test("server migration preserves a conflicting local conversation before deleting server copy", async ({
  page,
  request,
}) => {
  const id = "legacy-conflict-" + Date.now();
  await request.put("/api/settings/session-storage", {
    data: { enabled: true },
  });
  await request.post("/api/chat", {
    data: {
      model_id: "missing",
      session_id: id,
      message: "server older transcript",
      store_session: true,
    },
  });
  const serverRuns = await (await request.get("/api/runs")).json();
  const runId = serverRuns.find((run: any) => run.session_id === id).id;
  await page.goto("/");
  await expect(
    page.getByRole("switch", { name: "服务端存储会话" }),
  ).toBeChecked();
  await page.evaluate(
    async ({ id, runId }) => {
      await new Promise<void>((resolve, reject) => {
        const opening = indexedDB.open("ai-studio-chat-history", 1);
        opening.onsuccess = () => {
          const db = opening.result,
            tx = db.transaction("records", "readwrite");
          tx.objectStore("records").put({
            key: "session:" + id,
            value: {
              id,
              title: "local newer transcript",
              local: true,
              messages: [
                { role: "user", content: "local newer transcript" },
                { role: "assistant", content: "local later answer" },
              ],
              updated_at: new Date().toISOString(),
            },
          });
          tx.objectStore("records").put({
            key: "run:" + runId,
            value: {
              id: runId,
              kind: "chat",
              name: "local newer run",
              status: "completed",
              output: "local run output must survive",
              local: true,
            },
          });
          tx.oncomplete = () => {
            db.close();
            resolve();
          };
          tx.onerror = () => reject(tx.error);
        };
        opening.onerror = () => reject(opening.error);
      });
    },
    { id, runId },
  );
  await request.put("/api/settings/session-storage", {
    data: { enabled: false },
  });
  await page.reload();
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/sessions/local-migration")).json())
          .sessions.length,
    )
    .toBe(0);
  const saved = await page.evaluate(
    async ({ id }) =>
      new Promise<any>((resolve, reject) => {
        const opening = indexedDB.open("ai-studio-chat-history", 1);
        opening.onsuccess = () => {
          const db = opening.result,
            tx = db.transaction("records", "readonly"),
            get = tx.objectStore("records").get("session:" + id);
          tx.oncomplete = () => {
            resolve(get.result?.value);
            db.close();
          };
          tx.onerror = () => reject(tx.error);
        };
      }),
    { id },
  );
  expect(saved.messages[0].content).toBe("local newer transcript");
  const savedRuns = await page.evaluate(
    async () =>
      new Promise<any[]>((resolve, reject) => {
        const opening = indexedDB.open("ai-studio-chat-history", 1);
        opening.onsuccess = () => {
          const db = opening.result,
            tx = db.transaction("records", "readonly"),
            get = tx.objectStore("records").getAll();
          tx.oncomplete = () => {
            resolve(
              get.result
                .filter((row: any) => row.key.startsWith("run:"))
                .map((row: any) => row.value),
            );
            db.close();
          };
          tx.onerror = () => reject(tx.error);
        };
      }),
  );
  expect(savedRuns.find((run: any) => run.id === runId).output).toBe(
    "local run output must survive",
  );
  expect(
    savedRuns.some((run: any) => run.id !== runId && run.status === "failed"),
  ).toBeTruthy();
  await expect(page.locator(".session-list")).toContainText(
    "server older transcript",
  );
});
