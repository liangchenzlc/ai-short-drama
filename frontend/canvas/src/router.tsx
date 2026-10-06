import { lazy, Suspense, useEffect } from "react";
import { createBrowserRouter, Outlet, useLocation } from "react-router";
import { CanvasRefreshShell } from "@/pages/canvas/canvas-refresh-shell";
import { FullScreenLoader, WorkspaceRouteLoader } from "@/components/ui/aceternity/full-screen-loader";
import UserLayout from "@/layouts/user-layout";

const CanvasProjectPage = lazy(() => import("@/pages/canvas/project"));
const CanvasLibraryPage = lazy(() => import("@/pages/canvas"));
const AssetsPage = lazy(() => import("@/pages/assets"));
const SettingsPage = lazy(() => import("@/pages/settings"));
const TasksPage = lazy(() => import("@/pages/tasks"));

function HostProjectList() {
    useEffect(() => { window.location.replace("/projects"); }, []);
    return <CanvasRefreshShell />;
}

function CanvasLayout() {
    const { pathname } = useLocation();
    const fallback = pathname.startsWith("/canvas/") ? <CanvasRefreshShell /> : <FullScreenLoader label="正在打开创作空间" detail="准备当前页面" />;
    return <UserLayout><Suspense fallback={fallback}><Outlet /></Suspense></UserLayout>;
}

export const router = createBrowserRouter([
    {
        element: <CanvasLayout />,
        children: [
            { path: "/canvas", element: <CanvasLibraryPage /> },
            { path: "/canvas/:id", element: <CanvasProjectPage /> },
            { path: "/assets", element: <Suspense fallback={<WorkspaceRouteLoader />}><AssetsPage /></Suspense> },
            { path: "/settings", element: <Suspense fallback={<WorkspaceRouteLoader />}><SettingsPage /></Suspense> },
            { path: "/tasks", element: <Suspense fallback={<WorkspaceRouteLoader />}><TasksPage /></Suspense> },
            { path: "*", element: <HostProjectList /> },
        ],
    },
], { basename: import.meta.env.BASE_URL });
