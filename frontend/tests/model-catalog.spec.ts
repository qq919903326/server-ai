import { test, expect } from "@playwright/test";
import { createServer } from "node:http";

test("key discovery populates model editor and chat choices", async ({
  page,
  request,
}) => {
  const calls: string[] = [];
  const server = createServer((req, res) => {
    calls.push(req.headers.authorization || "");
    res.setHeader("Content-Type", "application/json");
    res.end(
      JSON.stringify({
        data: [{ id: "catalog-alpha" }, { id: "catalog-beta" }],
      }),
    );
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as { port: number }).port;
  let id = "";
  try {
    await page.goto("/");
    await page.getByRole("menuitem", { name: "模型连接" }).click();
    await page.getByRole("button", { name: "新建模型" }).click();
    const drawer = page.getByRole("dialog");
    id = await drawer.locator("input[disabled]").inputValue();
    await drawer.getByLabel("名称", { exact: true }).fill("动态模型测试");
    await drawer
      .getByLabel("API 地址", { exact: true })
      .fill(`http://127.0.0.1:${port}/v1`);
    await drawer
      .getByLabel("API Key", { exact: true })
      .fill("test-catalog-only");
    await drawer.getByLabel("名称", { exact: true }).click();
    await expect(drawer.getByText("已获取 2 个模型")).toBeVisible();
    const model = drawer.getByRole("combobox", {
      name: "模型名称",
      exact: true,
    });
    await model.fill("catalog-beta");
    await drawer.getByRole("button", { name: "保存配置" }).click();
    await expect(drawer).not.toBeVisible();
    const saved = (await (await request.get("/api/models")).json()).find(
      (m: any) => m.id === id,
    );
    expect(saved.available_models).toEqual(["catalog-alpha", "catalog-beta"]);
    expect(saved.api_key).toBeUndefined();
    expect(calls).toEqual(["Bearer test-catalog-only"]);
    await page.getByRole("menuitem", { name: "对话工作台" }).click();
    await page.locator(".conversation-toolbar .ant-select").click();
    await page
      .locator(".ant-select-item-option")
      .filter({ hasText: "动态模型测试 · catalog-alpha" })
      .click();
    await expect(page.locator(".conversation-toolbar")).toContainText(
      "动态模型测试 · catalog-alpha",
    );
  } finally {
    if (id) await request.delete("/api/models/" + id);
    await new Promise<void>((resolve, reject) =>
      server.close((e) => (e ? reject(e) : resolve())),
    );
  }
});
