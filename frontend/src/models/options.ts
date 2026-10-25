export function modelOptions(
  models: Record<string, any>[],
): Record<string, any>[] {
  return models.flatMap((config) => {
    const variants: string[] = Array.from(
      new Set([config.model, ...(config.available_models || [])]),
    );
    return variants.map((name) => ({
      ...config,
      id:
        name === config.model
          ? config.id
          : config.id + "::" + encodeURIComponent(name),
      name: config.name + " · " + name,
      model: name,
    }));
  });
}
