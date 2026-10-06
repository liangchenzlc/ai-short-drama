import { useLayoutEffect, type ReactNode } from "react";
import "@/styles/workspace-product.css";

/** 保留源编辑器的尺寸和浮层边界，不安装源整站导航。 */
export default function UserLayout({ children }: { children: ReactNode }) {
    useLayoutEffect(() => {
        document.body.classList.add("app-user-overlays");
        return () => { document.body.classList.remove("app-user-overlays"); };
    }, []);

    return (
        <div className="app-user-workspace h-dvh overflow-hidden text-foreground">
            <div className="app-workspace-shell flex h-dvh min-h-0 w-full flex-col overflow-hidden">
                <div className="app-workspace-main-row flex min-h-0 min-w-0 flex-1 overflow-hidden">
                    <div className="app-workspace-stage relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
                        <div className="relative min-h-0 min-w-0 flex-1 overflow-hidden">{children}</div>
                    </div>
                </div>
            </div>
        </div>
    );
}
