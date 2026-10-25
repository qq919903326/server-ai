import { test, expect } from "@playwright/test";

test("Jenkins connection can be saved, edited and selected for Agent use", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("menuitem", { name: "MCP 工具" }).click();
  await page.getByRole("button", { name: "连接 Jenkins", exact: true }).click();
  const drawer = page.getByRole("dialog");
  const id = await drawer.locator("input[disabled]").inputValue();
  try {
    await expect(drawer.getByLabel("名称", { exact: true })).toHaveValue("Jenkins");
    await drawer.getByLabel("Jenkins 地址", { exact: true }).fill("ci.example:8080/jenkins");
    await drawer.getByLabel("账号", { exact: true }).fill("operator");
    await drawer.getByLabel("密码 / API Token", { exact: true }).fill("ci-test-password");
    await drawer.getByRole("button", { name: "保存配置" }).click();
    const card = page.locator(".ant-card").filter({ has: page.getByRole("heading", { name: "Jenkins", exact: true }) });
    await expect(card).toContainText("JENKINS · 直连");
    const config = (await (await request.get("/api/mcp")).json()).find((m: any) => m.id === id);
    expect(config.service_type).toBe("jenkins");
    expect(config.url).toBe("http://ci.example:8080/jenkins");
    expect(config.allowed_tools).toContain("trigger_build");
    expect(config.has_password).toBe(true);
    expect(config.password).toBeUndefined();
    await card.getByRole("button").filter({ has: page.locator('[aria-label="edit"]') }).click();
    await expect(drawer.getByLabel("密码 / API Token", { exact: true })).toHaveValue("");
    await drawer.getByRole("button", { name: "保存配置" }).click();
    await page.getByRole("menuitem", { name: "对话工作台" }).click();
    await page.getByRole("combobox", { name: "MCP 工具服务", exact: true }).click();
    await page.locator(".ant-select-item-option").filter({ hasText: "Jenkins" }).click();
    await expect(page.locator(".context-panel")).toContainText("Jenkins");
  } finally {
    await request.delete("/api/mcp/" + id);
  }
});
