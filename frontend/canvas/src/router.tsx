import { lazy, Suspense, useEffect } from "react";
import { createBrowserRouter } from "react-router";
import { CanvasRefreshShell } from "@/pages/canvas/canvas-refresh-shell";
import { FullScreenLoader } from "@/components/ui/aceternity/full-screen-loader";
import UserLayout from "@/layouts/user-layout";
import { settingsPath } from "@/lib/settings-navigation";

const CanvasProjectPage = lazy(() => import("@/pages/canvas/project"));
function HostPage({ to }: { to: string }) {
    useEffect(() => { window.location.replace(to); }, [to]);
    return <FullScreenLoader label="正在返回工作台" detail="打开当前工作台页面" />;
}

export const router = createBrowserRouter([
    {
        path: "/canvas/:id",
        element: <UserLayout><Suspense fallback={<CanvasRefreshShell />}><CanvasProjectPage /></Suspense></UserLayout>,
    },
    { path: "/settings", element: <HostPage to={settingsPath()} /> },
    { path: "/assets/*", element: <HostPage to="/assets" /> },
    { path: "/tasks/*", element: <HostPage to="/tasks" /> },
    { path: "*", element: <HostPage to="/projects" /> },
], { basename: import.meta.env.BASE_URL });
