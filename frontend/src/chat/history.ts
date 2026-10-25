import { api } from "../api";

export type LocalRecord = Record<string, any> & { id: string };
let writes: Promise<unknown> = Promise.resolve();

function transaction<T>(
  mode: IDBTransactionMode,
  action: (items: IDBObjectStore) => IDBRequest<T> | void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open("ai-studio-chat-history", 1);
    opening.onupgradeneeded = () =>
      opening.result.createObjectStore("records", { keyPath: "key" });
    opening.onerror = () => reject(opening.error);
    opening.onsuccess = () => {
      const db = opening.result,
        tx = db.transaction("records", mode);
      let request: IDBRequest<T> | void;
      try {
        request = action(tx.objectStore("records"));
      } catch (error) {
        db.close();
        reject(error);
        return;
      }
      tx.oncomplete = () => {
        resolve(request?.result as T);
        db.close();
      };
      tx.onerror = () => {
        reject(tx.error);
        db.close();
      };
      tx.onabort = () => {
        reject(tx.error || new Error("本地历史事务未提交，服务端副本尚未清除"));
        db.close();
      };
    };
  });
}

function write(action: (items: IDBObjectStore) => void) {
  const next = writes
    .catch(() => {})
    .then(() => transaction("readwrite", action));
  writes = next;
  return next;
}

export async function saveLocalSession(session: LocalRecord) {
  const row = { ...session, local: true, updated_at: new Date().toISOString() };
  await write((items) => {
    items.put({ key: "session:" + row.id, value: row });
    items.put({ key: "active", value: row.id });
  });
}
export async function saveLocalRun(run: LocalRecord) {
  await write((items) =>
    items.put({ key: "run:" + run.id, value: { ...run, local: true } }),
  );
}
export async function localRecords(
  kind: "session" | "run",
): Promise<LocalRecord[]> {
  await writes;
  const all = await transaction<any[]>("readonly", (items) => items.getAll());
  return all
    .filter((row) => row.key.startsWith(kind + ":"))
    .map((row) => row.value)
    .sort((a, b) =>
      (b.updated_at || b.started_at || "").localeCompare(
        a.updated_at || a.started_at || "",
      ),
    );
}
export async function localSession(
  id: string,
): Promise<LocalRecord | undefined> {
  await writes;
  return (
    await transaction<any>("readonly", (items) => items.get("session:" + id))
  )?.value;
}
export async function activeLocalSession() {
  await writes;
  const id = (
    await transaction<any>("readonly", (items) => items.get("active"))
  )?.value;
  return id ? localSession(id) : undefined;
}
export async function clearActiveLocalSession() {
  await write((items) => items.delete("active"));
}
export function mergeRecords(
  server: Record<string, any>[],
  local: Record<string, any>[],
) {
  const records = new Map(server.map((row) => [row.id, row]));
  local.forEach((row) => records.set(row.id, row));
  return [...records.values()].sort((a, b) =>
    (b.updated_at || b.started_at || "").localeCompare(
      a.updated_at || a.started_at || "",
    ),
  );
}

let migration: Promise<void> | undefined;
function importArchiveRecord(
  items: IDBObjectStore,
  kind: "session" | "run",
  row: LocalRecord,
) {
  const content = (value: LocalRecord) =>
    JSON.stringify(
      kind === "session"
        ? value.messages
        : {
            output: value.output,
            trace: value.trace,
            status: value.status,
            error: value.error,
          },
    );
  const stamp =
    Date.parse(row.updated_at || row.finished_at || row.started_at || "") || 0;
  const copyBase = "import-" + row.id.slice(0, 32) + "-" + stamp;
  const insert = (id: string, copy: boolean, attempt = 0) => {
    if (attempt > 100) {
      items.transaction.abort();
      return;
    }
    const key = kind + ":" + id,
      request = items.get(key);
    request.onsuccess = () => {
      const existing = request.result?.value;
      if (!existing) {
        items.put({
          key,
          value: {
            ...row,
            id,
            local: true,
            ...(copy
              ? {
                  name: (row.name || row.title || "会话") + "（服务端旧副本）",
                  title: (row.title || row.name || "会话") + "（服务端旧副本）",
                }
              : {}),
          },
        });
        return;
      }
      if (content(existing) === content(row)) return;
      // Keep both branches. Never overwrite a local continuation, including one
      // made after a previous import whose server acknowledgement was interrupted.
      insert(
        copy ? copyBase + "-" + (attempt + 1) : copyBase,
        true,
        copy ? attempt + 1 : 0,
      );
    };
  };
  insert(row.id, false);
}
export function migrateServerHistory() {
  if (migration) return migration;
  migration = (async () => {
    for (let page = 0; page < 100; page++) {
      const archive = await api("/sessions/local-migration");
      if (!archive.sessions.length && !archive.runs.length) return;
      // One durable transaction must commit before acknowledging deletion.
      await write((items) => {
        for (const session of archive.sessions)
          importArchiveRecord(items, "session", session);
        for (const run of archive.runs) importArchiveRecord(items, "run", run);
      });
      const ack = Object.fromEntries(
        ["sessions", "runs"].map((kind) => [
          kind,
          archive[kind].map((row: any) => ({
            id: row.id,
            updated_at: row.updated_at,
          })),
        ]),
      );
      const result = await api("/sessions/local-migration", "POST", ack);
      if (!result.deleted)
        throw new Error(
          "会话迁移期间设置或历史发生变化，请刷新重试；服务端副本尚未清除。",
        );
    }
    throw new Error("旧会话较多，请刷新继续迁移到本地。");
  })().finally(() => {
    migration = undefined;
  });
  return migration;
}
