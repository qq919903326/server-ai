import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
const external = process.env.AI_E2E_BASE_URL;
const root = fileURLToPath(new URL("../", import.meta.url));
const python = join(
  root,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
export default defineConfig({
  timeout: 60000,
  testDir: "./tests",
  workers: 1,
  use: {
    baseURL: external || "http://127.0.0.1:8001",
    channel: "msedge",
    headless: true,
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 1000 },
  },
  reporter: "list",
  // Never migrate real user sessions while exercising browser privacy behavior.
  webServer: external
    ? undefined
    : {
        command: `"${python}" -m uvicorn backend.app:app --host 127.0.0.1 --port 8001 --no-access-log`,
        cwd: root,
        url: "http://127.0.0.1:8001/api/health",
        reuseExistingServer: false,
        env: {
          AI_DATA_DIR: mkdtempSync(join(tmpdir(), "ai-studio-e2e-")),
          APP_TOKEN: "",
          STORE_SESSIONS: "false",
        },
        stdout: "ignore",
        stderr: "pipe",
        timeout: 60000,
      },
});
