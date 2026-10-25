import { test, expect, type Page, type Locator } from "@playwright/test";

async function connect(page: Page, from: Locator, to: Locator) {
  const a = await from.boundingBox(),
    b = await to.boundingBox();
  if (!a || !b) throw new Error("Missing graph handle");
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 18 });
  await page.mouse.up();
}

test("drag, connect, reject cycles, persist layout, and show executed branch", async ({
  page,
  request,
}) => {
  const id = "graph-ui-" + Date.now(),
    name = "图形连线回归测试";
  await request.put("/api/workflows/" + id, {
    data: {
      name,
      entry_id: "start",
      steps: [
        {
          id: "start",
          label: "输入",
          type: "template",
          prompt: "{{input}}",
          position: { x: 0, y: 90 },
          next: "check",
        },
        {
          id: "check",
          label: "检查条件",
          type: "condition",
          contains: "yes",
          position: { x: 320, y: 90 },
          next: "pass",
          otherwise: "fail",
        },
        {
          id: "pass",
          label: "通过",
          type: "template",
          prompt: "通过 {{input}}",
          position: { x: 640, y: -30 },
        },
        {
          id: "fail",
          label: "拒绝",
          type: "template",
          prompt: "拒绝 {{input}}",
          position: { x: 640, y: 260 },
        },
      ],
    },
  });
  try {
    await page.goto("/");
    await page.getByRole("menuitem", { name: "Workflow 工作流" }).click();
    let card = page
      .locator(".resource-card")
      .filter({ has: page.getByRole("heading", { name, exact: true }) });
    await card.getByRole("button", { name: "图形编辑" }).click();
    const dialog = page.getByRole("dialog"),
      node = dialog.getByTestId("wf-node-pass");
    await expect(node).toBeVisible();
    await node.hover({ position: { x: 60, y: 30 } });
    const box = await node.boundingBox();
    if (!box) throw new Error("No node");
    await page.mouse.move(box.x + 60, box.y + 30);
    await page.mouse.down();
    await page.mouse.move(box.x + 110, box.y + 80, { steps: 14 });
    await page.mouse.up();
    await node.click();
    await dialog.getByLabel("节点名称", { exact: true }).fill("通过输出");
    await dialog.getByRole("button", { name: "Fit View", exact: true }).click();
    await connect(
      page,
      dialog.getByTestId("wf-out-check"),
      dialog.getByTestId("wf-in-fail"),
    );
    await connect(
      page,
      dialog.getByTestId("wf-otherwise-check"),
      dialog.getByTestId("wf-in-pass"),
    );
    await page.screenshot({
      path: "../work/workflow-editor.png",
      fullPage: true,
    });
    await connect(
      page,
      dialog.getByTestId("wf-out-pass"),
      dialog.getByTestId("wf-in-check"),
    );
    await expect(
      page.getByText("连线会形成循环，不能保存", { exact: true }),
    ).toBeVisible();
    await dialog
      .getByRole("button", { name: "保存工作流", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    let saved = (await (await request.get("/api/workflows")).json()).find(
      (w: any) => w.id === id,
    );
    expect(saved.steps.find((s: any) => s.id === "check").next).toBe("fail");
    expect(saved.steps.find((s: any) => s.id === "check").otherwise).toBe(
      "pass",
    );
    expect(saved.steps.find((s: any) => s.id === "pass").label).toBe(
      "通过输出",
    );
    expect(saved.steps.find((s: any) => s.id === "pass").position.x).not.toBe(
      640,
    );
    await page.reload();
    await page.getByRole("menuitem", { name: "Workflow 工作流" }).click();
    card = page
      .locator(".resource-card")
      .filter({ has: page.getByRole("heading", { name, exact: true }) });
    await card.getByRole("button", { name: "图形编辑" }).click();
    await expect(
      page.getByRole("dialog").getByTestId("wf-node-pass"),
    ).toContainText("通过输出");
    await page.getByRole("button", { name: "关闭编辑器", exact: true }).click();
    await card.getByRole("button", { name: "运行流程", exact: true }).click();
    await page.getByLabel("工作流输入", { exact: true }).fill("yes");
    await page.getByRole("button", { name: "开始执行", exact: true }).click();
    await expect(page.getByTestId("workflow-output")).toHaveText("拒绝 yes");
    const run = page.getByRole("dialog");
    await expect(run.getByTestId("wf-node-fail")).toHaveClass(
      /wf-status-completed/,
    );
    await expect(run.getByTestId("wf-node-pass")).toHaveClass(
      /wf-status-skipped/,
    );
    await expect(run.getByTestId("wf-node-fail")).toBeVisible();
    await expect(run.locator('.react-flow__node[data-id="fail"]')).toHaveCSS(
      "visibility",
      "visible",
    );
    await page.screenshot({ path: "../work/workflow-run.png", fullPage: true });
    await page.getByRole("button", { name: "关闭运行视图" }).click();
    await page.getByRole("menuitem", { name: "运行记录" }).click();
    await page
      .getByRole("row")
      .filter({ has: page.getByRole("cell", { name, exact: true }) })
      .getByRole("button", { name: "查看详情" })
      .click();
    await expect(
      page.getByRole("dialog").getByTestId("wf-node-pass"),
    ).toHaveClass(/wf-status-skipped/);
  } finally {
    await request.delete("/api/workflows/" + id);
    const runs = await (await request.get("/api/runs")).json();
    for (const r of runs.filter((r: any) => r.workflow_id === id))
      await request.delete("/api/runs/" + r.id);
  }
});

test("new visual workflow can add and delete nodes without JSON", async ({
  page,
  request,
}) => {
  const name = "新建画布测试-" + Date.now();
  let id = "";
  try {
    await page.goto("/");
    await page.getByRole("menuitem", { name: "Workflow 工作流" }).click();
    await page.getByRole("button", { name: "新建工作流" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("工作流名称", { exact: true }).fill(name);
    await dialog.locator('.ant-select[aria-label="节点类型"]').click();
    await page
      .locator(".ant-select-item-option")
      .filter({ hasText: "文本模板" })
      .click();
    await dialog
      .getByLabel("节点提示词", { exact: true })
      .fill("完成 {{input}}");
    await dialog.getByTestId("add-condition").click();
    await dialog.getByRole("button", { name: "删除节点", exact: true }).click();
    await dialog
      .getByRole("button", { name: "保存工作流", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    const saved = (await (await request.get("/api/workflows")).json()).find(
      (w: any) => w.name === name,
    );
    id = saved.id;
    expect(saved.steps).toHaveLength(1);
    expect(saved.steps[0].type).toBe("template");
  } finally {
    if (id) await request.delete("/api/workflows/" + id);
  }
});

test("tool argument drafts survive selection and JSON import uses new values", async ({
  page,
  request,
}) => {
  const id = "args-ui-" + Date.now(),
    name = "工具参数编辑回归";
  await request.put("/api/workflows/" + id, {
    data: {
      name,
      entry_id: "db",
      steps: [
        {
          id: "db",
          type: "tool",
          server_id: "remote",
          tool_name: "query",
          arguments: { sql: "SELECT 1" },
        },
      ],
    },
  });
  try {
    await page.goto("/");
    await page.getByRole("menuitem", { name: "Workflow 工作流" }).click();
    await page
      .locator(".resource-card")
      .filter({ has: page.getByRole("heading", { name, exact: true }) })
      .getByRole("button", { name: "图形编辑" })
      .click();
    await page.getByRole("button", { name: "JSON 配置", exact: true }).click();
    const area = page.getByLabel("工作流 JSON", { exact: true });
    const data = JSON.parse(await area.inputValue());
    data.steps[0].arguments = { sql: "SELECT 2" };
    await area.fill(JSON.stringify(data));
    await page.getByRole("button", { name: "应用到画布", exact: true }).click();
    await expect(
      page.getByLabel("工具参数 JSON", { exact: true }),
    ).toContainText("SELECT 2");
    await page.getByLabel("工具参数 JSON", { exact: true }).fill("{ invalid");
    await page.getByTestId("add-template").click();
    await page.getByRole("button", { name: "保存工作流", exact: true }).click();
    await expect(
      page.getByText("请先修正工具参数 JSON", { exact: true }),
    ).toBeVisible();
    await page.getByRole("dialog").getByTestId("wf-node-db").click();
    await expect(page.getByLabel("工具参数 JSON", { exact: true })).toHaveValue(
      "{ invalid",
    );
  } finally {
    await request.delete("/api/workflows/" + id);
  }
});
