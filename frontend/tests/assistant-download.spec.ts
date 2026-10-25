import { test, expect } from "@playwright/test";

test("HTTP assistant flow discovers desktop libraries, retries download, and revokes devices", async ({
  page,
}) => {
  let online = false,
    discovered = false,
    revoked = false,
    downloads = 0,
    mcpReads = 0;
  const project = () => ({
    id: "desktop-demo",
    name: "本机目录",
    source: "desktop",
    online,
    ready: online,
  });
  await page.addInitScript(() => {
    sessionStorage.setItem("ai-token", "mock-app-token");
    Object.defineProperty(window, "isSecureContext", { value: false });
    Object.defineProperty(window, "showDirectoryPicker", { value: undefined });
    Object.defineProperty(navigator, "locks", { value: undefined });
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/projects/assistant-download") {
      expect(route.request().method()).toBe("POST");
      expect(route.request().headers().authorization).toBe(
        "Bearer mock-app-token",
      );
      expect(route.request().postData()).toBeNull();
      downloads++;
      return route.fulfill(
        downloads === 1
          ? { status: 503, json: { detail: "助手模板暂不可用" } }
          : {
              contentType: "application/vnd.microsoft.portable-executable",
              body: "MZ-mock",
              headers: {
                "Content-Disposition":
                  "attachment; filename=AIStudioAssistant.exe",
              },
            },
      );
    }
    if (path === "/api/projects/assistants/device-demo") {
      revoked = true;
      online = false;
      return route.fulfill({ json: { ok: true } });
    }
    if (path === "/api/projects")
      return route.fulfill({ json: revoked ? [] : discovered ? [project()] : [] });
    if (path === "/api/projects/assistant-status")
      return route.fulfill({
        json: {
          devices: revoked
            ? []
            : [{ id: "device-demo", kind: "desktop", online }],
        },
      });
    if (path === "/api/mcp") {
      mcpReads++;
      return route.fulfill({
        json: online
          ? [
              {
                id: "project-desktop-demo",
                name: "本机目录",
                service_type: "project",
                enabled: true,
                allowed_tools: ["read_file"],
              },
            ]
          : [],
      });
    }
    return route.fulfill({
      json: path === "/api/settings/session-storage" ? { enabled: true } : [],
    });
  });
  await page.goto("/");
  await page.getByRole("menuitem", { name: "本地文件库" }).click();
  await expect(
    page.getByRole("button", { name: "浏览器直接授权", exact: true }),
  ).toBeDisabled();
  const button = page.getByRole("button", { name: "下载 Windows 授权助手" });
  await button.click();
  await expect(
    page.getByText("助手模板暂不可用", { exact: true }),
  ).toBeVisible();
  await expect(button).toBeEnabled();
  const download = page.waitForEvent("download");
  await button.click();
  expect((await download).suggestedFilename()).toBe("AIStudioAssistant.exe");
  const offlineReads = mcpReads;
  discovered = true;
  online = true;
  const checkbox = page.getByRole("checkbox", {
    name: "选择文件库 本机目录",
    exact: true,
  });
  await expect(checkbox).toBeEnabled({ timeout: 12000 });
  await expect.poll(() => mcpReads).toBeGreaterThan(offlineReads);
  const reads = mcpReads;
  await page.waitForTimeout(3500);
  expect(mcpReads).toBe(reads);
  online = false;
  await expect(checkbox).toBeDisabled({ timeout: 12000 });
  await expect(
    page.getByText("已断线 / 授权已失效", { exact: true }),
  ).toBeVisible();
  online = true;
  await expect(checkbox).toBeEnabled({ timeout: 12000 });
  await checkbox.check();
  await page
    .getByRole("button", { name: "使用所选文件库（1）", exact: true })
    .click();
  await expect(
    page
      .getByRole("combobox", { name: "本地文件库", exact: true })
      .locator(
        'xpath=ancestor::div[contains(concat(" ",normalize-space(@class)," ")," ant-select ")][1]',
      ),
  ).toContainText("本机目录");
  await page.getByRole("menuitem", { name: "本地文件库" }).click();
  await page.getByRole("button", { name: "撤销设备授权" }).click();
  await page.getByRole("button", { name: "确 定" }).click();
  await expect(
    page.getByText("设备授权已撤销；如需继续使用，请重新授权"),
  ).toBeVisible();
  await expect(checkbox).toHaveCount(0);
});
