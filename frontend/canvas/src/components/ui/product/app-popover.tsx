import { Popover, type PopoverProps } from "antd";
import type { CSSProperties, ReactNode } from "react";

type PopoverSemanticName = "root" | "container" | "content";
type AppPopoverProps = Omit<PopoverProps, "classNames" | "styles"> & {
    classNames?: Partial<Record<PopoverSemanticName, string>>;
    styles?: Partial<Record<PopoverSemanticName, CSSProperties>>;
};

// 保留源的表面/内容两层语义，映射到宿主 Ant Design 5 的 body 与内容层。
export function AppPopover({ classNames, styles, content, ...props }: AppPopoverProps) {
    const renderContent = (): ReactNode => (
        <div className={classNames?.content} style={styles?.content}>
            {typeof content === "function" ? content() : content}
        </div>
    );
    return (
        <Popover
            {...props}
            classNames={{ root: classNames?.root, body: classNames?.container }}
            styles={{ root: styles?.root, body: styles?.container }}
            content={renderContent}
        />
    );
}
