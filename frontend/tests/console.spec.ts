import { test, expect } from "@playwright/test";

test("console navigation, skill persistence, workflow execution and history", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "从一个想法开始" }),
  ).toBeVisible();
  await page.getByRole("menuitem", { name: "模型连接" }).click();
  await expect(page.getByText("DeepSeek", { exact: true })).toBeVisible();
  await page.getByRole("menuitem", { name: "MCP 工具" }).click();
  await page.getByRole("button", { name: "新建连接" }).click();
  const mcpDrawer = page.getByRole("dialog");
  const mcpId = await mcpDrawer.locator("input[disabled]").inputValue();
  try {
    await expect(mcpDrawer.getByLabel("本地执行命令")).toHaveCount(0);
    await mcpDrawer.getByLabel("名称", { exact: true }).fill("远程测试配置");
    await mcpDrawer
      .getByLabel("远程 MCP URL", { exact: true })
      .fill("https://example.com/mcp");
    await mcpDrawer.getByRole("button", { name: "保存配置" }).click();
    await expect(
      page.getByRole("heading", { name: "远程测试配置" }),
    ).toBeVisible();
  } finally {
    await request.delete("/api/mcp/" + mcpId);
  }
  await page.getByRole("menuitem", { name: "Skills 技能" }).click();
  await page.getByRole("button", { name: "新建技能" }).click();
  const drawer = page.getByRole("dialog");
  const id = await drawer.locator("input[disabled]").inputValue();
  try {
    await drawer.getByLabel("名称", { exact: true }).fill("浏览器测试技能");
    await drawer.getByLabel("简介", { exact: true }).fill("验证配置保存");
    await drawer
      .getByLabel("SKILL.md", { exact: true })
      .fill("# 测试技能\n回答保持简洁。");
    await drawer.getByRole("button", { name: "保存配置" }).click();
    await expect(
      page.getByRole("heading", { name: "浏览器测试技能" }),
    ).toBeVisible();
    await page.reload();
    await page.getByRole("menuitem", { name: "Skills 技能" }).click();
    await expect(
      page.getByRole("heading", { name: "浏览器测试技能" }),
    ).toBeVisible();
  } finally {
    await request.delete("/api/skills/" + id);
  }
  const workflowId = "browser-test-" + Date.now();
  try {
    await request.put("/api/workflows/" + workflowId, {
      data: {
        name: "浏览器验证流程",
        steps: [
          { id: "result", type: "template", prompt: "执行完成：{{input}}" },
        ],
      },
    });
    await page.getByRole("menuitem", { name: "Workflow 工作流" }).click();
    const card = page
      .locator(".resource-card")
      .filter({ has: page.getByRole("heading", { name: "浏览器验证流程" }) });
    await card.getByRole("button", { name: "运行流程" }).click();
    await page.getByPlaceholder("输入流程需要处理的内容").fill("测试任务");
    await page.getByRole("button", { name: "开始执行" }).click();
    await expect(
      page.getByText("执行完成：测试任务", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "关闭", exact: true }).click();
    await page.getByRole("menuitem", { name: "运行记录" }).click();
    await expect(
      page.getByRole("cell", { name: "浏览器验证流程", exact: true }),
    ).toBeVisible();
  } finally {
    await request.delete("/api/workflows/" + workflowId);
    const runs = await (await request.get("/api/runs")).json();
    for (const run of runs.filter((r: any) => r.name === "浏览器验证流程"))
      await request.delete("/api/runs/" + run.id);
  }
  await page.getByRole("menuitem", { name: "对话工作台" }).click();
  await page.screenshot({ path: "../work/console.png", fullPage: true });
  expect(errors).toEqual([]);
});
