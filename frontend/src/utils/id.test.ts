import { afterEach, expect, it, vi } from "vitest";
import { uuid } from "./id";

afterEach(() => vi.unstubAllGlobals());

it("prefers native randomUUID and preserves the crypto receiver", () => {
  const expected = "01234567-89ab-4cde-8f01-23456789abcd";
  const source = {
    randomUUID: vi.fn(function (this: unknown) {
      expect(this).toBe(source);
      return expected;
    }),
    getRandomValues: vi.fn(),
  };
  vi.stubGlobal("crypto", source);
  expect(uuid()).toBe(expected);
  expect(source.randomUUID).toHaveBeenCalledOnce();
  expect(source.getRandomValues).not.toHaveBeenCalled();
});

it("uses getRandomValues when randomUUID is missing on HTTP and sets v4/variant bits", () => {
  const source = {
    getRandomValues: vi.fn(function (this: unknown, bytes: Uint8Array) {
      expect(this).toBe(source);
      expect(bytes).toBeInstanceOf(Uint8Array);
      expect(bytes.length).toBe(16);
      bytes.set([0, 1, 2, 3, 4, 5, 0xff, 7, 0x7f, 9, 10, 11, 12, 13, 14, 15]);
      return bytes;
    }),
  };
  vi.stubGlobal("crypto", source);
  const id = uuid();
  expect(id).toBe("00010203-0405-4f07-bf09-0a0b0c0d0e0f");
  expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  expect(id.replaceAll("-", "").slice(0, 12)).toBe("000102030405");
  expect("prompt_" + id.slice(0, 6)).toBe("prompt_000102");
  expect(source.getRandomValues).toHaveBeenCalledOnce();
});

it.each([undefined, {}, { randomUUID: undefined, getRandomValues: undefined }])(
  "throws an explicit error when secure randomness is unavailable (%j)",
  (source) => {
    vi.stubGlobal("crypto", source);
    expect(() => uuid()).toThrow("当前浏览器缺少安全随机源");
  },
);

it("propagates secure random source failures", () => {
  vi.stubGlobal("crypto", {
    getRandomValues: () => { throw new Error("random source failed"); },
  });
  expect(() => uuid()).toThrow("random source failed");
});
