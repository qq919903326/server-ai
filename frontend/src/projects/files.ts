// File System Access handles are the authority; server paths never open local files.
export type Directory = FileSystemDirectoryHandle & {
  queryPermission(options: { mode: "read" }): Promise<PermissionState>;
  requestPermission(options: { mode: "read" }): Promise<PermissionState>;
  entries(): AsyncIterableIterator<[string, FileSystemHandle]>;
};

function integer(value: unknown, fallback: number, min: number, max: number) {
  const n = value === undefined ? fallback : value;
  if (typeof n !== "number" || !Number.isSafeInteger(n) || n < min || n > max)
    throw new Error("分页参数超出允许范围");
  return n;
}

export async function readDirectory(
  root: Directory,
  method: string,
  args: Record<string, any>,
): Promise<any> {
  if (!["list_directory", "file_info", "read_text"].includes(method))
    throw new Error("只允许读取文件和目录");
  if ((await root.queryPermission({ mode: "read" })) !== "granted")
    throw new Error("文件库目录需要重新授权");
  const path = args.path ?? "";
  if (
    typeof path !== "string" ||
    path.length > 2048 ||
    /[:\x00-\x1f]/.test(path) ||
    /^[\\/]/.test(path)
  )
    throw new Error("路径必须位于授权项目内");
  const parts = path
    .replaceAll("\\", "/")
    .split("/")
    .filter((p) => p && p !== ".");
  if (parts.includes("..")) throw new Error("不能访问项目外的上级目录");
  let directory: FileSystemDirectoryHandle = root;
  const walk = method === "list_directory" ? parts : parts.slice(0, -1);
  for (const part of walk)
    directory = await directory.getDirectoryHandle(part, { create: false });
  if (method === "list_directory") {
    const offset = integer(args.offset, 0, 0, 1000000),
      limit = integer(args.limit, 100, 1, 100);
    const entries: any[] = [];
    let index = 0,
      truncated = false;
    for await (const [name, handle] of (directory as Directory).entries()) {
      if (index++ < offset) continue;
      if (entries.length === limit) {
        truncated = true;
        break;
      }
      entries.push({ name, type: handle.kind, readable: true });
    }
    return {
      entries,
      truncated,
      next_offset: truncated ? offset + entries.length : null,
      note: "目录变化可能影响分页顺序",
    };
  }
  let handle: FileSystemHandle = directory;
  if (parts.length) {
    const name = parts[parts.length - 1];
    try {
      handle = await directory.getFileHandle(name, { create: false });
    } catch (e) {
      if (method !== "file_info") throw e;
      handle = await directory.getDirectoryHandle(name, { create: false });
    }
  }
  if (handle.kind === "directory") {
    if (method === "read_text") throw new Error("请选择项目内的文本文件");
    return { path, type: "directory", size: null, modified_at: null };
  }
  const file = await (handle as FileSystemFileHandle).getFile();
  if (method === "file_info")
    return {
      path,
      type: "file",
      size: file.size,
      modified_at: new Date(file.lastModified).toISOString(),
    };
  const offset = integer(args.offset, 0, 0, Number.MAX_SAFE_INTEGER),
    max = integer(args.max_bytes, 16384, 1, 16384);
  const bytes = new Uint8Array(
    await file.slice(offset, offset + max).arrayBuffer(),
  );
  if (bytes.includes(0))
    throw new Error("不支持读取二进制文件，请选择 UTF-8 文本");
  const text = new TextDecoder("utf-8").decode(bytes),
    truncated = offset + bytes.length < file.size;
  return {
    text,
    offset,
    bytes_read: bytes.length,
    next_offset: truncated ? offset + bytes.length : null,
    truncated,
    encoding: "utf-8",
    decode_errors: text.includes("\ufffd"),
  };
}
