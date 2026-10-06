import { Drawer, type DrawerProps } from "antd";
import { useCallback, useLayoutEffect, useState, type CSSProperties } from "react";

// 用途：产品侧栏壳。消费 token 由内容自己负责。默认 destroyOnHidden，可选 flush 去内边距。
export type AppDrawerProps = DrawerProps & {
    flush?: boolean;
    focusable?: { trap?: boolean };
};

export function AppDrawer({ flush = false, destroyOnHidden = true, styles, className, rootClassName, focusable, panelRef, ...props }: AppDrawerProps) {
    const [panel, setPanel] = useState<HTMLDivElement | null>(null);
    const capturePanel = useCallback((element: HTMLDivElement | null) => {
        setPanel(element);
        if (typeof panelRef === "function") panelRef(element);
        else if (panelRef) panelRef.current = element;
    }, [panelRef]);

    useLayoutEffect(() => {
        const drawer = panel?.closest(".ant-drawer");
        drawer?.querySelectorAll<HTMLElement>(":scope > [data-sentinel]").forEach((sentinel) => {
            sentinel.tabIndex = focusable?.trap === false ? -1 : 0;
        });
    }, [focusable?.trap, panel]);
    const mergeResolvedStyles = (resolved: unknown) => {
        const base = resolved && typeof resolved === "object" ? (resolved as Record<string, unknown>) : {};
        if (!flush) return base;
        const body = base.body && typeof base.body === "object" ? (base.body as CSSProperties) : {};
        return { ...base, body: { ...body, padding: 0 } };
    };
    const mergedStyles = mergeResolvedStyles(styles);

    return <Drawer destroyOnHidden={destroyOnHidden} className={className} rootClassName={rootClassName} {...props} panelRef={capturePanel} styles={mergedStyles as DrawerProps["styles"]} />;
}
