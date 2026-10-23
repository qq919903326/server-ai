import { test, expect } from "@playwright/test";

test("database MCP templates can be configured and selected in chat", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "配置数据库 MCP", exact: true })
    .click();
  await page.getByRole("button", { name: "连接 MySQL", exact: true }).click();
  const drawer = page.getByRole("dialog");
  const id = await drawer.locator("input[disabled]").inputValue();
  try {
    await expect(drawer.getByLabel("名称", { exact: true })).toHaveValue(
      "MySQL",
    );
    await expect(drawer.getByLabel("认证 Token 的环境变量名")).toHaveValue(
      "MYSQL_MCP_TOKEN",
    );
    await expect(drawer.getByLabel("启用", { exact: true })).toBeChecked();
    await drawer
      .getByLabel("远程 MCP URL", { exact: true })
      .fill("https://mysql-mcp.example/mcp");
    await drawer.getByRole("button", { name: "保存配置" }).click();
    await expect(
      page.getByRole("heading", { name: "MySQL", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "连接 PostgreSQL", exact: true })
      .click();
    await expect(
      page.getByRole("dialog").getByLabel("名称", { exact: true }),
    ).toHaveValue("PostgreSQL");
    await expect(
      page.getByRole("dialog").getByLabel("认证 Token 的环境变量名"),
    ).toHaveValue("POSTGRES_MCP_TOKEN");
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "关闭", exact: true })
      .click();
    await page.getByRole("menuitem", { name: "对话工作台" }).click();
    await page.getByRole("combobox", { name: "MCP 工具服务", exact: true }).click();
    await page.locator('.ant-select-item-option').filter({hasText:'MySQL'}).click();
    await expect(page.locator(".context-panel")).toContainText("MySQL");
    const config = (await (await request.get("/api/mcp")).json()).find(
      (m: any) => m.id === id,
    );
    expect(config.allowed_tools).toEqual([
      "list_tables",
      "describe_table",
      "query",
    ]);
    expect(config.enabled).toBe(true);
  } finally {
    await request.delete("/api/mcp/" + id);
  }
});

test('unconfigured MCP connections show why selection is disabled', async ({page,request}) => {
  const id = 'disabled-test-' + Date.now();
  await request.put('/api/mcp/' + id, {data:{name:'未完成的数据库',transport:'http',url:'https://example.com/mcp',enabled:false,allowed_tools:['query']}});
  try {
    await page.goto('/');
    await page.getByRole('combobox',{name:'MCP 工具服务',exact:true}).click();
    const option = page.locator('.ant-select-item-option').filter({hasText:'未完成的数据库（未启用）'});
    await expect(option).toBeVisible();
    await expect(option).toHaveClass(/ant-select-item-option-disabled/);
  } finally {await request.delete('/api/mcp/' + id);}
});
