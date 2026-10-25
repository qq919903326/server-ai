/** Generate an ID with secure randomness, including on public HTTP origins. */
export function uuid(): string {
  const source = globalThis.crypto;
  if (typeof source?.randomUUID === "function") return source.randomUUID();
  if (typeof source?.getRandomValues !== "function")
    throw new Error("无法生成 ID：当前浏览器缺少安全随机源（crypto.getRandomValues）");

  const bytes = source.getRandomValues(new Uint8Array(16));
  // RFC 9562 UUID v4: version 4 and RFC variant bits.
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
