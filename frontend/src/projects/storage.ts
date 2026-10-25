export async function browserStore(
  operation: "get" | "put" | "delete",
  key: string,
  value?: unknown,
): Promise<any> {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open("ai-studio-local-projects", 1);
    opening.onupgradeneeded = () => opening.result.createObjectStore("items");
    opening.onerror = () => reject(opening.error);
    opening.onsuccess = () => {
      const db = opening.result;
      const transaction = db.transaction(
        "items",
        operation === "get" ? "readonly" : "readwrite",
      );
      const items = transaction.objectStore("items");
      let request: IDBRequest;
      try {
        request =
          operation === "get"
            ? items.get(key)
            : operation === "put"
              ? items.put(value, key)
              : items.delete(key);
      } catch (error) {
        db.close();
        reject(error);
        return;
      }
      transaction.oncomplete = () => {
        resolve(request.result);
        db.close();
      };
      transaction.onerror = () => {
        reject(transaction.error);
        db.close();
      };
      transaction.onabort = () => {
        reject(transaction.error);
        db.close();
      };
    };
  });
}
