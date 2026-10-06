export async function initializeWorkspaceState<T>({ loadWorkspace, applySession, restoreModelConfig, restoreProjects }: {
    loadWorkspace: () => Promise<T>;
    applySession: (payload: T) => Promise<void>;
    restoreModelConfig: () => Promise<void>;
    restoreProjects: () => Promise<unknown>;
}) {
    const payload = await loadWorkspace();
    await applySession(payload);
    await restoreModelConfig();
    await restoreProjects();
}
