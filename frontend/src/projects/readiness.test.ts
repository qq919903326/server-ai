import { expect, it, vi } from "vitest";
import {
  checkProjectPermissions,
  isProjectReadable,
  projectAvailabilityKey,
  type Project,
} from "./readiness";

it("desktop and legacy assistants need online + ready, never browser permissions", async () => {
  const granted = vi.fn(async () => {
    throw new Error("no browser APIs on HTTP");
  });
  const items: Project[] = [
    {
      id: "desktop",
      name: "Desktop",
      source: "desktop",
      online: true,
      ready: true,
    },
    { id: "legacy", name: "Legacy", online: true, ready: true },
  ];
  expect(
    (await checkProjectPermissions(items, granted)).every(isProjectReadable),
  ).toBe(true);
  expect(granted).not.toHaveBeenCalled();
  expect(isProjectReadable({ ...items[0], online: false })).toBe(false);
  expect(isProjectReadable({ ...items[1], ready: false })).toBe(false);
});

it("browser projects still require a locally granted handle as well as readiness", async () => {
  const project = {
    id: "web",
    name: "Web",
    source: "browser",
    online: true,
    ready: true,
  };
  const granted = vi.fn(async () => false);
  const [checked] = await checkProjectPermissions([project], granted);
  expect(granted).toHaveBeenCalledWith("web");
  expect(isProjectReadable(checked)).toBe(false);
  expect(isProjectReadable({ ...checked, localGranted: true })).toBe(true);
  expect(
    isProjectReadable({ ...checked, localGranted: true, ready: false }),
  ).toBe(false);
});

it("poll keys change on discovery, disconnect, readiness or removal, not reorder or repeated polls", () => {
  const a = {
    id: "a",
    name: "A",
    source: "desktop",
    online: true,
    ready: true,
  };
  const b = { ...a, id: "b" };
  const key = projectAvailabilityKey([a, b]);
  expect(projectAvailabilityKey([b, { ...a }])).toBe(key);
  for (const items of [
    [a],
    [a, { ...b, online: false }],
    [a, { ...b, ready: false }],
    [],
  ])
    expect(projectAvailabilityKey(items)).not.toBe(key);
});
