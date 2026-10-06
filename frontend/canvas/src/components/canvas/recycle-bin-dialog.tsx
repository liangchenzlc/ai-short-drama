import { App, Button } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { RotateCcw, Trash2, X } from "lucide-react";

import { ProjectPreview } from "@/components/canvas/canvas-project-card";
import { cn } from "@/lib/utils";
import { restoreLocalCanvasProjectFromRecycle } from "@/services/local-workspace-repository";
import { purgeRecycledProject, refreshRecycledProjects, removeRecycledProject } from "@/services/canvas-recycle";
import { recycledProjectPreview } from "@/services/canvas-recycle-state";
import { resourceFileUrl } from "@/services/api/resources";
import { assertUserScope, captureUserScope, isUserScopeAbandonedError } from "@/lib/user-scope-guard";
import { useCanvasHistoryStore } from "@/stores/canvas/use-canvas-history-store";

export function RecycleBinDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
    const closeButtonRef = useRef<HTMLButtonElement>(null);
    const { message } = App.useApp();
    const deletedProjects = useCanvasHistoryStore((state) => state.deletedProjects);
    const [selectedDeleted, setSelectedDeleted] = useState<string[]>([]);
    const [deleteConfirmationOpen, setDeleteConfirmationOpen] = useState(false);
    const [restoring, setRestoring] = useState(false);
    const restoringRef = useRef(false);
    const [deleting, setDeleting] = useState(false);
    const [refreshing, setRefreshing] = useState(false);
    const [refreshError, setRefreshError] = useState("");
    const [refreshIndex, setRefreshIndex] = useState(0);
    const previewProjects = useMemo(() => new Map(deletedProjects.map(item => [item.id,
        recycledProjectPreview(item, id => new URL(resourceFileUrl(id), window.location.origin).href),
    ])), [deletedProjects]);

    const allDeletedProjectIds = useMemo(() => deletedProjects.map((project) => project.id), [deletedProjects]);
    const allDeletedSelected = allDeletedProjectIds.length > 0 && allDeletedProjectIds.every((id) => selectedDeleted.includes(id));

    useEffect(() => {
        if (!open) return;
        let active = true;
        setRefreshing(true);
        setRefreshError("");
        void refreshRecycledProjects().then(() => {
            if (!active) return;
            const ids = new Set(useCanvasHistoryStore.getState().deletedProjects.map(item => item.id));
            setSelectedDeleted(current => current.filter(id => ids.has(id)));
        }).catch(error => {
            if (active && !isUserScopeAbandonedError(error)) setRefreshError(error instanceof Error ? error.message : "回收站读取失败");
        }).finally(() => { if (active) setRefreshing(false); });
        return () => { active = false; };
    }, [open, refreshIndex]);

    useEffect(() => {
        if (!open) return;
        setSelectedDeleted([]);
        setDeleteConfirmationOpen(false);
        const previousOverflow = document.body.style.overflow;
        document.body.style.overflow = "hidden";
        closeButtonRef.current?.focus();
        return () => {
            document.body.style.overflow = previousOverflow;
        };
    }, [open]);

    useEffect(() => {
        if (!open) return;
        const closeOnEscape = (event: KeyboardEvent) => {
            if (event.key !== "Escape") return;
            if (deleteConfirmationOpen) setDeleteConfirmationOpen(false);
            else onClose();
        };
        window.addEventListener("keydown", closeOnEscape);
        return () => {
            window.removeEventListener("keydown", closeOnEscape);
        };
    }, [deleteConfirmationOpen, onClose, open]);

    if (!open || typeof document === "undefined") return null;

    const restoreSelectedProjects = async () => {
        if (restoringRef.current) return;
        const expected = captureUserScope();
        restoringRef.current = true;
        setRestoring(true);
        try {
            for (const id of selectedDeleted) {
                assertUserScope(expected);
                const item = useCanvasHistoryStore.getState().deletedProjects.find((entry) => entry.id === id);
                if (!item?.project) throw new Error("回收站缺少作品快照，未恢复画布");
                await restoreLocalCanvasProjectFromRecycle(item.project, item.archiveKey);
                assertUserScope(expected);
                await removeRecycledProject(item, expected);
                assertUserScope(expected);
                setSelectedDeleted(current => current.filter(value => value !== id));
            }
        } catch (error) {
            if (!isUserScopeAbandonedError(error)) message.error(error instanceof Error ? error.message : "恢复失败，请重试");
        } finally {
            restoringRef.current = false;
            setRestoring(false);
        }
    };

    const permanentlyDeleteProjects = async (ids: string[]) => {
        if (restoringRef.current) return;
        const expected = captureUserScope();
        restoringRef.current = true;
        setDeleting(true);
        try {
            for (const id of ids) {
                assertUserScope(expected);
                const item = useCanvasHistoryStore.getState().deletedProjects.find(entry => entry.id === id);
                if (!item) continue;
                await purgeRecycledProject(item, expected);
                setSelectedDeleted(current => current.filter(value => value !== id));
            }
            setDeleteConfirmationOpen(false);
        } catch (error) {
            if (!isUserScopeAbandonedError(error)) message.error(error instanceof Error ? error.message : "删除失败，请重试");
        } finally {
            restoringRef.current = false;
            setDeleting(false);
        }
    };

    return createPortal(
        <div
            className="recycle-bin-overlay"
            onMouseDown={(event) => {
                if (event.target === event.currentTarget) onClose();
            }}
        >
            <section className="recycle-bin-dialog" role="dialog" aria-modal="true" aria-labelledby="recycle-bin-title">
                <header className="recycle-bin-header">
                    <h2 id="recycle-bin-title">回收站</h2>
                    <button ref={closeButtonRef} type="button" className="recycle-bin-close" aria-label="关闭回收站" onClick={onClose}>
                        <X />
                    </button>
                </header>

                <div className="recycle-bin-viewport">
                    {refreshError ? <div className="recycle-bin-empty" role="alert">{refreshError} <Button onClick={() => setRefreshIndex(value => value + 1)}>重新读取</Button></div> : null}
                    {refreshing && deletedProjects.length === 0 ? <div className="recycle-bin-empty" role="status">正在读取回收站…</div> : deletedProjects.length === 0 && !refreshError ? (
                        <div className="recycle-bin-empty">回收站是空的</div>
                    ) : (
                        <div className="recycle-bin-grid">
                            {deletedProjects.map((item) => {
                                const checked = selectedDeleted.includes(item.id);
                                return (
                                    <article key={`${item.id}-${item.deletedAt}`} className={cn("recycle-bin-card", checked && "is-selected")}>
                                        <label className="recycle-bin-checkbox">
                                            <input
                                                type="checkbox"
                                                checked={checked}
                                                onChange={(event) => setSelectedDeleted((current) => (event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id)))}
                                                aria-label={`选择 ${item.title || "未命名项目"}`}
                                            />
                                        </label>
                                        <div className="recycle-bin-preview">{previewProjects.get(item.id) ? <ProjectPreview project={previewProjects.get(item.id)!} emptyVariant="libtv" /> : <div className="canvas-project-empty is-libtv size-full" />}</div>
                                        <div className="recycle-bin-card-body">
                                            <h3>{item.title || "未命名项目"}</h3>
                                            <time>{formatTimelineDate(item.deletedAt || item.updatedAt)}</time>
                                        </div>
                                    </article>
                                );
                            })}
                        </div>
                    )}
                </div>

                <footer className="recycle-bin-footer">
                    <label className="recycle-bin-select-all">
                        <input type="checkbox" aria-label="全选回收站项目" disabled={!allDeletedProjectIds.length} checked={allDeletedSelected} onChange={(event) => setSelectedDeleted(event.target.checked ? allDeletedProjectIds : [])} />
                        <span>全选</span>
                        {selectedDeleted.length ? <span className="recycle-bin-selected-count">已选择 {selectedDeleted.length} 项</span> : null}
                    </label>
                    <div className="recycle-bin-actions">
                        <Button danger disabled={restoring || deleting || refreshing || !selectedDeleted.length} icon={<Trash2 className="size-4" />} onClick={() => setDeleteConfirmationOpen(true)}>
                            彻底删除
                        </Button>
                        <Button loading={restoring} disabled={deleting || refreshing || !selectedDeleted.length} icon={<RotateCcw className="size-4" />} onClick={() => void restoreSelectedProjects()} aria-label="恢复到项目列表">
                            恢复
                        </Button>
                    </div>
                </footer>

                {deleteConfirmationOpen ? (
                    <div
                        className="recycle-delete-confirm-backdrop"
                        onMouseDown={(event) => {
                            if (event.target === event.currentTarget) setDeleteConfirmationOpen(false);
                        }}
                    >
                        <div className="recycle-delete-confirm" role="alertdialog" aria-modal="true" aria-labelledby="recycle-delete-title" aria-describedby="recycle-delete-description">
                            <h3 id="recycle-delete-title">确认彻底删除？</h3>
                            <p id="recycle-delete-description">将永久删除已选择的 {selectedDeleted.length} 个项目，删除后无法恢复。</p>
                            <div className="recycle-delete-confirm-actions">
                                <Button disabled={deleting} onClick={() => setDeleteConfirmationOpen(false)}>取消</Button>
                                <Button
                                    danger
                                    type="primary"
                                    loading={deleting}
                                    onClick={() => void permanentlyDeleteProjects(selectedDeleted)}
                                >
                                    确认删除
                                </Button>
                            </div>
                        </div>
                    </div>
                ) : null}
            </section>
        </div>,
        document.body,
    );
}

function formatTimelineDate(isoString: string) {
    if (!isoString) return "--";
    const date = new Date(isoString);
    if (!Number.isFinite(date.getTime())) return "--";
    return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, "0")}/${String(date.getDate()).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}
