import { it, expect } from "vitest";
import { readDirectory } from "./files";

const content = "hello browser project";
const file = {
  kind: "file",
  getFile: async () => ({
    size: content.length,
    lastModified: 0,
    slice: (a: number, b: number) => new Blob([content.slice(a, b)]),
  }),
};
const root: any = {
  kind: "directory",
  name: "demo",
  queryPermission: async () => "granted",
  getFileHandle: async (name: string) => {
    if (name !== "readme.txt") throw new Error("missing");
    return file;
  },
  getDirectoryHandle: async () => {
    throw new Error("missing");
  },
  async *entries() {
    yield ["readme.txt", file];
  },
};

it("reads only relative files inside the granted directory and paginates bytes", async () => {
  expect(
    (
      await readDirectory(root, "read_text", {
        path: "readme.txt",
        max_bytes: 5,
      })
    ).text,
  ).toBe("hello");
  expect(
    (await readDirectory(root, "list_directory", {})).entries[0].name,
  ).toBe("readme.txt");
  for (const path of [
    "../secret",
    "/secret",
    "C:\\secret",
    "readme.txt:ads",
    "sub/../../secret",
  ]) {
    await expect(readDirectory(root, "read_text", { path })).rejects.toThrow();
  }
  await expect(
    readDirectory(root, "read_text", { path: "readme.txt", max_bytes: 20000 }),
  ).rejects.toThrow();
});

it("does not prompt or read when browser permission was revoked", async () => {
  await expect(
    readDirectory(
      { ...root, queryPermission: async () => "prompt" },
      "read_text",
      { path: "readme.txt" },
    ),
  ).rejects.toThrow("授权");
  await expect(readDirectory(root, "write_file", {})).rejects.toThrow();
});
