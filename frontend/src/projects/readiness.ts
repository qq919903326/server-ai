export type Project = {
  id: string;
  name: string;
  source?: string;
  online: boolean;
  ready: boolean;
  localGranted?: boolean;
  error?: string;
};

// Unmarked projects were created by the original local assistant.
export function isBrowserProject(project: Project) {
  return project.source === "browser";
}

export function isProjectReadable(project: Project) {
  return !!(
    project.online &&
    project.ready &&
    (!isBrowserProject(project) || project.localGranted)
  );
}

export async function checkProjectPermissions(
  projects: Project[],
  granted: (id: string) => Promise<boolean>,
) {
  return Promise.all(
    projects.map(async (project) =>
      isBrowserProject(project)
        ? { ...project, localGranted: await granted(project.id) }
        : project,
    ),
  );
}

export function projectAvailabilityKey(projects: Project[]) {
  return JSON.stringify(
    projects
      .map((p) => [p.id, p.name, p.source || "desktop", isProjectReadable(p)])
      .sort((a, b) => String(a[0]).localeCompare(String(b[0]))),
  );
}
