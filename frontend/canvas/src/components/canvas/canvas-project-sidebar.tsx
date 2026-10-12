import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowUpRight, BookOpenText, ChevronLeft, ChevronRight, Crosshair, FolderKanban, GripVertical, Images, LocateFixed, LoaderCircle, Palette, Plus, Search, Settings2, X } from "lucide-react";
import { Link } from "react-router";

import { episodePath, projectPath } from "@host/app/paths";
import { sourceEpisodeChapter, type CanvasSourceChapterPayload, type CanvasSourceEpisodeSummary } from "@/services/host-source-project";
import type { CanvasSourceProjectReader } from "@/pages/canvas/use-canvas-source-project";
import type { CanvasSourceChapterStatus } from "@/pages/canvas/use-canvas-upload";

export const CANVAS_PROJECT_CHAPTER_DND_TYPE = "application/x-infinite-canvas-project-chapter";
export type CanvasProjectChapterPayload = CanvasSourceChapterPayload;

const CHAPTER_ROW_HEIGHT = 36;

type CanvasProjectSidebarProps = {
    projectId: string;
    source: CanvasSourceProjectReader;
    chapterStatus: CanvasSourceChapterStatus | null;
    characterCount: number;
    hasCanvasStyle: boolean;
    onAddChapter: (chapter: CanvasProjectChapterPayload) => void | Promise<void>;
    onLocateStyle: () => void;
    onOpenAssets: () => void;
};

export function CanvasProjectSidebar({ projectId, source, chapterStatus, characterCount, hasCanvasStyle, onAddChapter, onLocateStyle, onOpenAssets }: CanvasProjectSidebarProps) {
    const [collapsed, setCollapsed] = useState(false);
    const [query, setQuery] = useState("");
    const [selectedId, setSelectedId] = useState("");
    const [addingId, setAddingId] = useState("");
    const [pendingScrollId, setPendingScrollId] = useState("");
    const scrollRef = useRef<HTMLDivElement>(null);
    const deferredQuery = useDeferredValue(query.trim().toLocaleLowerCase("zh-CN"));
    const projectDetail = source.overview;
    const styleText = projectDetail?.project.style_text || "";
    const orderedUnits = useMemo(() => projectDetail?.episodes.slice().sort((left, right) => left.position - right.position) || [], [projectDetail?.episodes]);
    const visibleUnits = useMemo(() => {
        if (source.loading) return [];
        if (!deferredQuery) return orderedUnits.map((unit, index) => ({ unit, index }));
        const numericQuery = /^\d+$/.test(deferredQuery) ? deferredQuery.replace(/^0+/, "") || "0" : "";
        return orderedUnits.reduce<Array<{ unit: CanvasSourceEpisodeSummary; index: number }>>((result, unit, index) => {
            const chapterNumber = String(unit.position);
            const matchesNumber = numericQuery && chapterNumber.startsWith(numericQuery);
            const matchesTitle = unit.title.toLocaleLowerCase("zh-CN").includes(deferredQuery);
            if (matchesNumber || matchesTitle) result.push({ unit, index });
            return result;
        }, []);
    }, [deferredQuery, orderedUnits, source.loading]);
    const selectedUnit = orderedUnits.find((unit) => unit.id === selectedId);
    const selectedUnitQuery = useQuery({
        queryKey: ["canvas-source-episode", source.contextKey, selectedId, selectedUnit?.content_version || ""],
        queryFn: async () => ({ result: await source.readEpisode(selectedId, selectedUnit!.content_version), contextKey: source.contextKey }),
        enabled: Boolean(selectedUnit) && !source.error && !source.loading, retry: false,
    });
    const selectedBody = selectedUnitQuery.data?.contextKey === source.contextKey && !selectedUnitQuery.isError ? selectedUnitQuery.data.result : undefined;
    const [insertError, setInsertError] = useState("");
    const sourceContextRef = useRef(source.contextKey);
    sourceContextRef.current = source.contextKey;
    const chapterError = chapterStatus?.error || insertError;
    useEffect(() => { setSelectedId(""); setAddingId(""); setInsertError(""); }, [source.contextKey]);
    const chapterVirtualizer = useVirtualizer({
        count: visibleUnits.length,
        getScrollElement: () => scrollRef.current,
        estimateSize: () => CHAPTER_ROW_HEIGHT,
        getItemKey: (index) => visibleUnits[index]?.unit.id || index,
        initialOffset: () => readStoredScroll(`canvas-project-chapters:${projectId}`),
        onChange: (instance, scrolling) => {
            if (!scrolling && instance.scrollOffset !== null) sessionStorage.setItem(`canvas-project-chapters:${projectId}`, String(instance.scrollOffset));
        },
        overscan: 12,
    });

    useEffect(() => {
        if (!pendingScrollId) return;
        const index = visibleUnits.findIndex(({ unit }) => unit.id === pendingScrollId);
        if (index < 0) return;
        chapterVirtualizer.scrollToIndex(index, { align: "center" });
        setPendingScrollId("");
    }, [chapterVirtualizer, pendingScrollId, visibleUnits]);

    useEffect(() => {
        if (!selectedId) return;
        const closePreview = (event: KeyboardEvent) => {
            if (event.key === "Escape") setSelectedId("");
        };
        document.addEventListener("keydown", closePreview);
        return () => document.removeEventListener("keydown", closePreview);
    }, [selectedId]);

    const revealSelectedChapter = () => {
        if (!selectedId) return;
        setQuery("");
        setPendingScrollId(selectedId);
    };
    const selectFirstSearchResult = () => {
        const first = visibleUnits[0];
        if (!first) return;
        setSelectedId(first.unit.id);
        chapterVirtualizer.scrollToIndex(0, { align: "center" });
    };
    const insertChapter = async (unit: CanvasSourceEpisodeSummary) => {
        if (addingId || chapterStatus?.loading) return;
        const expectedContext = source.contextKey;
        setAddingId(unit.id);
        setInsertError("");
        try {
            await onAddChapter(sourceEpisodeChapter(unit, projectId));
        } catch (error) {
            if (sourceContextRef.current === expectedContext) setInsertError(error instanceof Error ? error.message : "章节插入失败，请重试");
        } finally {
            if (sourceContextRef.current === expectedContext) setAddingId("");
        }
    };

    if (collapsed) {
        return (
            <aside className="canvas-project-sidebar is-collapsed relative z-[var(--z-panel)] hidden w-11 shrink-0 flex-col items-center border-r border-border bg-background/94 py-2 backdrop-blur-xl lg:flex">
                <button type="button" className="grid size-7 place-items-center rounded-md text-foreground/55 hover:bg-surface-hover" title="展开项目侧栏" aria-label="展开项目侧栏" onClick={() => setCollapsed(false)}>
                    <ChevronRight className="size-4" />
                </button>
                <Link to={`/projects/${projectId}/canvases`} className="canvas-project-sidebar-back-link mt-2 grid size-7 place-items-center rounded-md text-foreground/55 hover:bg-surface-hover" title="返回项目画布列表">
                    <FolderKanban className="size-4" />
                </Link>
            </aside>
        );
    }

    return (
        <aside className="canvas-project-sidebar relative z-[var(--z-panel)] hidden w-[var(--canvas-sidebar-width)] shrink-0 flex-col border-r border-border bg-background/94 backdrop-blur-xl lg:flex">
            <header className="canvas-project-sidebar-header flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border px-2.5">
                <Link to={`/projects/${projectId}/canvases`} className="flex min-w-0 items-center gap-2 text-xs font-semibold" title="返回项目画布列表">
                    <FolderKanban className="size-3.5 shrink-0" />
                    <span className="truncate">{projectDetail?.project.name || "项目空间"}</span>
                </Link>
                <button type="button" className="grid size-7 shrink-0 place-items-center rounded-md text-foreground/45 hover:bg-surface-hover" title="收起项目侧栏" aria-label="收起项目侧栏" onClick={() => setCollapsed(true)}>
                    <ChevronLeft className="size-4" />
                </button>
            </header>

            <section className="canvas-project-sidebar-style shrink-0 border-b border-border/70 p-2">
                <div className="mb-1.5 flex h-5 items-center justify-between px-1 text-[var(--fs-label)] font-medium text-foreground/48">
                    <span className="flex items-center gap-1.5">
                        <Palette className="size-3.5" />
                        项目画风
                    </span>
                    <a href={projectPath(projectId)} className="grid size-6 place-items-center rounded text-foreground/35 hover:bg-surface-hover hover:text-foreground" title="打开来源项目设置" aria-label="打开来源项目设置">
                        <Settings2 className="size-3.5" />
                    </a>
                </div>
                <button
                    type="button"
                    disabled={!hasCanvasStyle}
                    onClick={onLocateStyle}
                    className="canvas-project-sidebar-style-button group flex h-11 w-full items-center gap-2 rounded-md px-1.5 text-left hover:bg-surface-hover disabled:cursor-default disabled:hover:bg-transparent"
                    title={hasCanvasStyle ? "定位画布中已保存的画风节点；来源画风快照暂不可同步" : "来源项目暂无可同步的画风快照；可从画布画风菜单添加"}
                >
                        <span className="grid h-8 w-12 shrink-0 place-items-center rounded border border-dashed border-border text-foreground/30">
                            <Palette className="size-3.5" />
                        </span>
                    <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-medium" title={styleText}>{hasCanvasStyle ? "已有画布画风" : styleText || "暂无来源画风说明"}</span>
                        <span className="mt-0.5 block truncate text-[var(--fs-micro)] text-foreground/38">来源画风快照暂不可同步</span>
                    </span>
                    {hasCanvasStyle ? <LocateFixed className="size-3.5 shrink-0 text-foreground/25 group-hover:text-[var(--workspace-accent)]" /> : null}
                </button>
            </section>

            <section className="canvas-project-sidebar-chapters flex min-h-0 flex-1 flex-col">
                <div className="canvas-project-sidebar-section-heading flex h-9 shrink-0 items-center justify-between px-3">
                    <span className="flex items-center gap-1.5 text-[var(--fs-label)] font-medium text-foreground/48">
                        <BookOpenText className="size-3.5" />
                        剧情章节 <span className="tabular-nums text-foreground/32">{orderedUnits.length.toLocaleString("zh-CN")}</span>
                    </span>
                    {selectedId ? (
                        <button type="button" onClick={revealSelectedChapter} className="grid size-6 place-items-center rounded text-foreground/35 hover:bg-surface-hover hover:text-foreground" title="回到当前章节" aria-label="回到当前章节">
                            <Crosshair className="size-3.5" />
                        </button>
                    ) : null}
                </div>
                <div className="shrink-0 px-2 pb-2">
                    <label className="canvas-project-sidebar-search flex h-8 items-center gap-1.5 rounded-md border border-border/75 bg-foreground/[.025] px-2 focus-within:border-[var(--workspace-accent)] focus-within:ring-2 focus-within:ring-[var(--workspace-accent-soft)]">
                        <Search className="size-3.5 shrink-0 text-foreground/32" />
                        <input
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                            onKeyDown={(event) => {
                                if (event.key === "Enter") selectFirstSearchResult();
                            }}
                            className="min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-foreground/28"
                            placeholder="搜索标题或输入章节序号"
                            aria-label="搜索项目章节"
                        />
                        {query ? (
                            <button type="button" onClick={() => setQuery("")} className="grid size-5 shrink-0 place-items-center rounded text-foreground/32 hover:bg-surface-hover hover:text-foreground" aria-label="清空章节搜索">
                                <X className="size-3" />
                            </button>
                        ) : null}
                    </label>
                    {deferredQuery && !source.loading ? <div className="mt-1 px-0.5 text-[var(--fs-micro)] tabular-nums text-foreground/35">找到 {visibleUnits.length.toLocaleString("zh-CN")} 章</div> : null}
                </div>

                <div ref={scrollRef} className="canvas-project-sidebar-chapter-scroll thin-scrollbar min-h-0 flex-1 overflow-y-auto overscroll-contain px-1.5" aria-label="项目章节列表">
                    {visibleUnits.length ? (
                        <div className="relative w-full" style={{ height: chapterVirtualizer.getTotalSize() }}>
                            {chapterVirtualizer.getVirtualItems().map((virtualItem) => {
                                const item = visibleUnits[virtualItem.index];
                                if (!item) return null;
                                const { unit } = item;
                                const active = unit.id === selectedId;
                                return (
                                    <div key={unit.id} className="absolute left-0 top-0 w-full" style={{ height: virtualItem.size, transform: `translateY(${virtualItem.start}px)` }}>
                                        <div
                                            draggable
                                            onDragStart={(event) => {
                                                const payload = sourceEpisodeChapter(unit, projectId);
                                                event.dataTransfer.setData(CANVAS_PROJECT_CHAPTER_DND_TYPE, JSON.stringify(payload));
                                                event.dataTransfer.effectAllowed = "copy";
                                            }}
                                            className={`canvas-project-sidebar-chapter-row group flex h-9 cursor-grab items-center gap-1 rounded-md px-1 text-xs active:cursor-grabbing ${active ? "bg-surface-active" : "hover:bg-surface-hover"}`}
                                        >
                                            <GripVertical className="size-3.5 shrink-0 text-foreground/22" />
                                            <button
                                                type="button"
                                                onClick={() => setSelectedId(unit.id)}
                                                className="flex min-w-0 flex-1 items-center gap-1.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--workspace-accent)]"
                                                aria-label={`预览第 ${unit.position} 章 ${unit.title}`}
                                            >
                                                <span className="w-7 shrink-0 text-[var(--fs-micro)] tabular-nums text-foreground/34">{String(unit.position).padStart(Math.max(2, String(orderedUnits.length).length), "0")}</span>
                                                <span className="min-w-0 flex-1 truncate text-foreground/68">{unit.title}</span>
                                            </button>
                                            <button
                                                type="button"
                                                disabled={Boolean(addingId) || Boolean(chapterStatus?.loading)}
                                                className="grid size-6 shrink-0 place-items-center rounded text-foreground/30 opacity-0 hover:bg-[var(--workspace-accent-soft)] hover:text-[var(--workspace-accent)] group-hover:opacity-100 focus-visible:opacity-100 disabled:cursor-wait"
                                                title="添加到画布中央"
                                                aria-label={`添加${unit.title}到画布`}
                                                onClick={() => void insertChapter(unit)}
                                            >
                                                {addingId === unit.id ? <LoaderCircle className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
                                            </button>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    ) : (
                        <div className="px-2 py-8 text-center text-xs text-foreground/40" role="status">{source.loading ? "正在读取来源项目…" : source.error || (orderedUnits.length ? "没有匹配的章节" : "暂无已保存分集")}{source.error ? <button type="button" onClick={() => void source.refreshSourceProject()} className="mt-2 block w-full text-[var(--workspace-accent)]">重新读取来源项目</button> : null}</div>
                    )}
                </div>
            </section>

            <section className="canvas-project-sidebar-assets shrink-0 border-t border-border/70 p-2">
                <button type="button" onClick={onOpenAssets} className="canvas-project-sidebar-assets-button flex h-9 w-full items-center gap-2 rounded-md border border-border/45 px-2 text-xs text-foreground/65 hover:border-[var(--workspace-accent)] hover:bg-[var(--workspace-accent-soft)]">
                    <Images className="size-3.5 text-foreground/42" />
                    <span className="flex-1 text-left">引用项目资产</span>
                    <span className="rounded bg-foreground/[.06] px-1.5 py-0.5 text-[var(--fs-micro)] tabular-nums text-foreground/45">{characterCount.toLocaleString("zh-CN")}</span>
                </button>
                <p className="mt-1 px-1 text-[var(--fs-micro)] text-foreground/48">可引用已采用角色；来源素材目录与其他资产暂不可读取</p>
                {chapterError || chapterStatus?.loading ? <p data-canvas-source-chapter-status role={chapterError ? "alert" : "status"} aria-live="polite" className="mt-1 break-words px-1 text-xs text-foreground">{chapterError || "正在读取章节正文…"}</p> : null}
            </section>

            {selectedUnit ? (
                <ChapterPreview
                    projectId={projectId}
                    unit={selectedUnit}
                    text={selectedBody?.novel.plain_text ?? ""}
                    missing={selectedBody?.novel.state === "missing"}
                    chapterNumber={selectedUnit.position}
                    loading={source.loading || selectedUnitQuery.isPending || selectedUnitQuery.isFetching}
                    loadError={selectedUnitQuery.isError ? (selectedUnitQuery.error instanceof Error ? selectedUnitQuery.error.message : "正文读取失败") : ""}
                    onRetry={() => { setSelectedId(""); void source.refreshSourceProject(); }}
                    insertError={chapterStatus?.chapterId === selectedUnit.id && chapterStatus.contentVersion === selectedUnit.content_version ? chapterStatus.error : ""}
                    adding={addingId === selectedUnit.id || Boolean(chapterStatus?.loading && chapterStatus.chapterId === selectedUnit.id)}
                    onAdd={() => insertChapter(selectedUnit)}
                    onClose={() => setSelectedId("")}
                />
            ) : null}
        </aside>
    );
}

function ChapterPreview({
    projectId,
    unit,
    text,
    missing,
    chapterNumber,
    loading,
    loadError,
    insertError,
    onRetry,
    adding,
    onAdd,
    onClose,
}: {
    projectId: string;
    unit: CanvasSourceEpisodeSummary;
    text: string;
    missing: boolean;
    chapterNumber: number;
    loading: boolean;
    loadError: string;
    insertError: string;
    onRetry: () => void;
    adding: boolean;
    onAdd: () => void | Promise<void>;
    onClose: () => void;
}) {
    return (
        <section
            className="absolute bottom-20 left-[calc(100%+8px)] top-14 z-[var(--z-panel-floating)] flex w-[var(--panel-width-compact)] flex-col overflow-hidden rounded-lg border border-border/85 bg-background/[.96] shadow-[0_18px_48px_rgba(0,0,0,.22)] backdrop-blur-xl"
            aria-label={`第 ${chapterNumber} 章预览`}
        >
            <header className="flex items-start gap-2 border-b border-border/70 px-3 py-2.5">
                <div className="min-w-0 flex-1">
                    <div className="text-[var(--fs-tiny)] tabular-nums text-foreground/38">第 {chapterNumber.toLocaleString("zh-CN")} 章</div>
                    <h2 className="mt-0.5 truncate text-sm font-semibold">{unit.title}</h2>
                </div>
                <button type="button" onClick={onClose} className="grid size-6 shrink-0 place-items-center rounded text-foreground/38 hover:bg-surface-hover hover:text-foreground" aria-label="关闭章节预览">
                    <X className="size-3.5" />
                </button>
            </header>
            <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto px-3 py-3">
                <p className="whitespace-pre-wrap break-words text-xs leading-5 text-foreground/58" role={loadError ? "alert" : "status"}>{loading ? "正在读取本章正文…" : loadError || (missing ? "本集尚未保存小说正文" : text || "已保存的小说正文为空")}</p>
                {insertError ? <p role="alert" className="mt-2 break-words text-xs text-foreground">{insertError}</p> : null}
                {loadError || insertError ? <button type="button" onClick={onRetry} className="mt-2 text-xs text-[var(--workspace-accent)]">刷新分集目录并重新选择</button> : null}
                {!loading && !loadError ? <div className="mt-2 text-[var(--fs-tiny)] tabular-nums text-foreground/35">{text.length.toLocaleString("zh-CN")} 字</div> : null}
            </div>
            <footer className="flex shrink-0 items-center justify-between border-t border-border/70 px-2 py-2">
                <button
                    type="button"
                    disabled={adding || loading || Boolean(loadError) || missing}
                    onClick={() => void onAdd()}
                    className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-[var(--workspace-accent)] hover:bg-[var(--workspace-accent-soft)] disabled:cursor-wait"
                >
                    {adding ? <LoaderCircle className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}添加到画布
                </button>
                <a href={episodePath(projectId, unit.id, "source")} className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs text-foreground/62 hover:bg-surface-hover hover:text-foreground">
                    打开编辑器
                    <ArrowUpRight className="size-3.5" />
                </a>
            </footer>
        </section>
    );
}

function readStoredScroll(key: string) {
    const value = Number(sessionStorage.getItem(key) || 0);
    return Number.isFinite(value) && value > 0 ? value : 0;
}
