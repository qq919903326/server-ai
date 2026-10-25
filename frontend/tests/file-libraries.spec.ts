import { test, expect } from "@playwright/test";

test("chat selects several local file libraries separately from remote MCP", async ({
  page,
  request,
}) => {
  const session = await (
    await request.post("/api/projects/browser-session", { data: {} })
  ).json();
  const ids: string[] = [];
  const alpha = "library-alpha-" + Date.now(),
    beta = "library-beta-" + Date.now();
  try {
    for (const name of [alpha, beta]) {
      const project = await (
        await request.post("/api/projects/browser", {
          data: { name, device_id: session.device_id },
        })
      ).json();
      ids.push(project.id);
    }
    await page.goto("/");
    const libraries = page.getByRole("combobox", {
      name: "本地文件库",
      exact: true,
    });
    await libraries
      .locator(
        'xpath=ancestor::div[contains(concat(" ",normalize-space(@class)," ")," ant-select ")][1]',
      )
      .click();
    await page
      .locator(".ant-select-item-option")
      .filter({ hasText: alpha })
      .click();
    await page
      .locator(".ant-select-item-option")
      .filter({ hasText: beta })
      .click();
    await page.keyboard.press("Escape");
    const field = libraries.locator(
      'xpath=ancestor::div[contains(concat(" ",normalize-space(@class)," ")," ant-select ")][1]',
    );
    await expect(field).toContainText(alpha);
    await expect(field).toContainText(beta);
    const remote = page.getByRole("combobox", {
      name: "MCP 工具服务",
      exact: true,
    });
    await remote
      .locator(
        'xpath=ancestor::div[contains(concat(" ",normalize-space(@class)," ")," ant-select ")][1]',
      )
      .click();
    await expect(
      page
        .locator(
          ".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option",
        )
        .filter({ hasText: alpha }),
    ).toHaveCount(0);
    await page.keyboard.press("Escape");
    let submitted: (value: any) => void = () => {};
    const sent = new Promise<any>((resolve) => (submitted = resolve));
    await page.route("**/api/chat", async (route) => {
      submitted(route.request().postDataJSON());
      await route.fulfill({
        contentType: "text/event-stream",
        body: 'event: done\ndata: {"status":"completed"}\n\n',
      });
    });
    await page.getByPlaceholder("输入你的问题或任务…").fill("读取这两个文件库");
    await page.getByRole("button", { name: "发送" }).click();
    expect((await sent).mcp_ids.sort()).toEqual(
      ids.map((id) => "project-" + id).sort(),
    );
    await page.getByRole("menuitem", { name: "本地文件库" }).click();
    await expect(
      page.getByRole("heading", { name: "本地文件库", exact: true }),
    ).toBeVisible();
  } finally {
    for (const id of ids) await request.delete("/api/projects/" + id);
    await request.delete("/api/projects/assistants/" + session.device_id);
  }
});

test("library page can grant and check several folders then use them together", async ({
  page,
  request,
}) => {
  const prefix = "bulk-libraries-" + Date.now();
  await page.addInitScript(
    ({ prefix }) => {
      let count = 0;
      Object.defineProperty(window, "showDirectoryPicker", {
        value: async () => {
          const root = await navigator.storage.getDirectory();
          return root.getDirectoryHandle(prefix + "-" + ++count, {
            create: true,
          });
        },
      });
    },
    { prefix },
  );
  let projects: any[] = [];
  try {
    await page.goto("/");
    await page.getByRole("menuitem", { name: "本地文件库" }).click();
    for (let i = 1; i <= 2; i++) {
      await page
        .getByRole("button", { name: "浏览器直接授权", exact: true })
        .click();
      const checkbox = page.getByRole("checkbox", {
        name: "选择文件库 " + prefix + "-" + i,
        exact: true,
      });
      await expect(checkbox).toBeEnabled({ timeout: 40000 });
      await checkbox.check();
    }
    projects = (await (await request.get("/api/projects")).json()).filter(
      (p: any) => p.name.startsWith(prefix),
    );
    await page
      .getByRole("button", { name: "使用所选文件库（2）", exact: true })
      .click();
    const field = page
      .getByRole("combobox", { name: "本地文件库", exact: true })
      .locator(
        'xpath=ancestor::div[contains(concat(" ",normalize-space(@class)," ")," ant-select ")][1]',
      );
    await expect(field).toContainText(prefix + "-1");
    await expect(field).toContainText(prefix + "-2");
  } finally {
    if (!projects.length)
      projects = (await (await request.get("/api/projects")).json()).filter(
        (p: any) => p.name.startsWith(prefix),
      );
    for (const project of projects)
      await request.delete("/api/projects/" + project.id);
    for (const device of new Set(projects.map((p) => p.device_id)))
      await request.delete("/api/projects/assistants/" + device);
  }
});
