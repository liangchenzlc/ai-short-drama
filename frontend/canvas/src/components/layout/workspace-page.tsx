import { Select } from "antd";
import { ChevronLeft, ChevronRight } from "lucide-react";

/* 自研轻量分页：页码胶囊 + 省略号 + 每页条数 + 总数，替代 AntD Pagination（无 AntD 残留样式，与工具栏容器同语言）。 */
function pageItems(current: number, pages: number): (number | "…")[] {
    if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
    if (current <= 4) return [1, 2, 3, 4, 5, "…", pages];
    if (current >= pages - 3) return [1, "…", pages - 4, pages - 3, pages - 2, pages - 1, pages];
    return [1, "…", current - 1, current, current + 1, "…", pages];
}

export function PaginationBar({
    current,
    pageSize,
    total,
    onChange,
    pageSizeOptions = [20, 50, 100],
    alwaysShow = false,
    itemLabel = "条",
}: {
    current: number;
    pageSize: number;
    total: number;
    onChange: (page: number, pageSize: number) => void;
    pageSizeOptions?: number[];
    alwaysShow?: boolean;
    itemLabel?: string;
}) {
    if (!alwaysShow && total <= pageSize && current === 1) return null;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const start = total === 0 ? 0 : (current - 1) * pageSize + 1;
    const end = total === 0 ? 0 : Math.min(total, current * pageSize);
    const items = pageItems(current, pages);
    return (
        <div className="app-pagination-bar admin-pagination-bar mt-4 flex min-h-10 min-w-0 items-center justify-end gap-2 px-2 py-1.5">
            <span className="admin-pagination-total">{total === 0 ? `共 0 ${itemLabel}` : `${start}-${end} / 共 ${total} ${itemLabel}`}</span>
            <Select size="small" value={pageSize} className="app-pagination-size" options={pageSizeOptions.map((size) => ({ value: size, label: `${size} ${itemLabel}/页` }))} onChange={(value) => onChange(1, Number(value))} />
            <div className="app-pagination-pages" role="navigation" aria-label="分页">
                <button type="button" className="app-pagination-btn app-pagination-prev" disabled={current <= 1} aria-label="上一页" onClick={() => onChange(current - 1, pageSize)}>
                    <ChevronLeft className="size-4" />
                </button>
                {items.map((item) =>
                    item === "…" ? (
                        <span key={`ellipsis-${items.indexOf(item)}`} className="app-pagination-ellipsis">
                            …
                        </span>
                    ) : (
                        <button key={item} type="button" className={`app-pagination-btn${item === current ? " is-active" : ""}`} aria-current={item === current ? "page" : undefined} onClick={() => onChange(item, pageSize)}>
                            {item}
                        </button>
                    ),
                )}
                <button type="button" className="app-pagination-btn app-pagination-next" disabled={current >= pages} aria-label="下一页" onClick={() => onChange(current + 1, pageSize)}>
                    <ChevronRight className="size-4" />
                </button>
            </div>
        </div>
    );
}
