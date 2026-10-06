export const loadCanvasProjectPage = () => import("@/pages/canvas/project");

export function preloadWorkspaceRoute(pathnameOrSlug: string) {
    if (/^\/canvas\/[^/]+\/?$/u.test(pathnameOrSlug)) void loadCanvasProjectPage();
}
