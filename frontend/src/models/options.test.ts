import { describe, it, expect } from "vitest";
import { modelOptions } from "./options";

describe("model selection", () => {
  it("keeps existing default IDs and exposes distinct provider variants", () => {
    const options = modelOptions([
      {
        id: "ds",
        name: "DeepSeek",
        model: "chat",
        enabled: true,
        available_models: ["chat", "reason/one"],
      },
    ]);
    expect(options.map((m) => [m.id, m.model])).toEqual([
      ["ds", "chat"],
      ["ds::reason%2Fone", "reason/one"],
    ]);
    expect(options[1].name).toBe("DeepSeek · reason/one");
  });
  it("keeps manual models usable without discovery", () => {
    expect(
      modelOptions([{ id: "custom", name: "Custom", model: "manual" }])[0].id,
    ).toBe("custom");
  });
});
