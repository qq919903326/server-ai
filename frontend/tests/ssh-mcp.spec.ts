import { test, expect } from "@playwright/test";

test("SSH connection needs only host, port and credentials and is selectable by Agent", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("menuitem", { name: "MCP 工具" }).click();
  await page.getByRole("button", { name: "连接 SSH", exact: true }).click();
  const drawer = page.getByRole("dialog");
  const id = await drawer.locator("input[disabled]").inputValue();
  try {
    await expect(drawer.getByLabel("远程 MCP URL", { exact: true })).toHaveCount(0);
    await expect(drawer.getByLabel("认证 Token 的环境变量名")).toHaveCount(0);
    await expect(drawer.getByLabel("SSH 端口", { exact: true })).toHaveValue("22");
    await drawer.getByLabel("名称", { exact: true }).fill("SSH 浏览器测试");
    await drawer.getByLabel("IP / 主机名", { exact: true }).fill("192.168.1.10");
    await drawer.getByLabel("SSH 端口", { exact: true }).fill("2222");
    await drawer.getByLabel("账号", { exact: true }).fill("operator");
    await drawer.getByLabel("密码", { exact: true }).fill("browser-ssh-secret");
    await drawer.getByRole("button", { name: "保存配置" }).click();
    const card = page.locator(".resource-card").filter({ has: page.getByRole("heading", { name: "SSH 浏览器测试" }) });
    await expect(card).toContainText("operator@192.168.1.10:2222");
    await expect(card).toContainText("已授权 4 个工具");
    await page.reload();
    await page.getByRole("menuitem", { name: "MCP 工具" }).click();
    await card.getByRole("button").filter({ has: page.getByRole("img", { name: "edit", exact: true }) }).click();
    await expect(drawer.getByLabel("密码", { exact: true })).toHaveValue("");
    await drawer.getByRole("button", { name: "保存配置" }).click();
    const configs = await (await request.get("/api/mcp")).json();
    const config = configs.find((item: any) => item.id === id);
    expect(config.has_password).toBe(true);
    expect(config.password).toBeUndefined();
    expect(config.port).toBe(2222);
    await page.getByRole("menuitem", { name: "对话工作台" }).click();
    await page.getByRole("combobox", { name: "MCP 工具服务", exact: true }).click();
    await page.locator(".ant-select-item-option").filter({ hasText: "SSH 浏览器测试" }).click();
    await expect(page.locator(".context-panel")).toContainText("SSH 浏览器测试");
  } finally {
    await request.delete("/api/mcp/" + id);
  }
});
