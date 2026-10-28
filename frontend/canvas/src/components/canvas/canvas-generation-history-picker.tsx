import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Input, Modal, Spin } from "antd";
import { FileAudio, FileVideo, Image as ImageIcon, Search } from "lucide-react";

import { CachedResourceImage } from "@/components/cached-resource-image";
import { useResourceVideoPlayback } from "@/hooks/use-resource-video-playback";
import {
    awaitCanvasGenerationHistoryDetailIfValid,
    assertCanvasGenerationHistoryTaskForInsert,
    canvasGenerationHistorySelectStillValid,
    insertableCanvasGenerationHistoryTasks,
    type CanvasGenerationHistorySelectGate,
} from "@/lib/canvas/canvas-generation-history";
import { generationTaskMode } from "@/lib/canvas/canvas-generation-task-sync";
import { captureUserScopeEpoch, getActiveUserScope } from "@/lib/user-scope";
import { captureUserScope, userScopeMatches } from "@/lib/user-scope-guard";
import { ownedResourceIdFromMediaRef, resourceIdFromStorageKey, resourceStorageKey } from "@/services/api/resources";
import { listGenerationTasks, queryGenerationTask, type GenerationTask } from "@/services/api/task-center";

type CanvasGenerationHistoryPickerProps = {
    open: boolean;
    projectId: string;
    onClose: () => void;
    onSelect: (task: GenerationTask) => Promise<void>;
};

export function CanvasGenerationHistoryPicker({ open, projectId, onClose, onSelect }: CanvasGenerationHistoryPickerProps) {
    const [keyword, setKeyword] = useState("");
    const [selectingId, setSelectingId] = useState<string | null>(null);
    const [selectionError, setSelectionError] = useState("");
    const selectionRequest = useRef<AbortController | null>(null);
    const selectionEpoch = useRef(0);
    const openRef = useRef(open);
    const projectIdRef = useRef(projectId);
    const mountedRef = useRef(true);
    const onSelectRef = useRef(onSelect);
    openRef.current = open;
    projectIdRef.current = projectId;
    onSelectRef.current = onSelect;
    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
        };
    }, []);
    const scope = getActiveUserScope();
    const cancelSelection = () => {
        selectionEpoch.current += 1;
        selectionRequest.current?.abort();
        selectionRequest.current = null;
        setSelectingId(null);
    };
    useEffect(() => { setSelectionError(""); return () => cancelSelection(); }, [open, projectId, scope]);
    const query = useQuery({
        queryKey: ["canvas-generation-history", scope, projectId],
        queryFn: ({ signal }) => listGenerationTasks(100, { projectId, activeOnly: false }, undefined, signal),
        enabled: open && Boolean(projectId.trim()),
        staleTime: 15_000,
        refetchInterval: open ? 5_000 : false,
    });
    const tasks = useMemo(
        () => insertableCanvasGenerationHistoryTasks(query.data || [], { projectId, keyword }),
        [keyword, projectId, query.data],
    );

    const liveSelectGate = (): CanvasGenerationHistorySelectGate => ({
        open: openRef.current,
        projectId: projectIdRef.current,
        epoch: captureUserScopeEpoch(),
        mounted: mountedRef.current,
        selectionEpoch: selectionEpoch.current,
    });

    const selectSummary = async (task: GenerationTask) => {
        if (selectionRequest.current || !task.id?.trim() || !projectId.trim()) return;
        const captured = liveSelectGate();
        if (!canvasGenerationHistorySelectStillValid(captured, captured)) return;
        const request = new AbortController();
        selectionRequest.current = request;
        setSelectingId(task.id);
        setSelectionError("");
        try {
            const resolved = await awaitCanvasGenerationHistoryDetailIfValid({
                detail: queryGenerationTask(task.id, { signal: request.signal }),
                captured,
                live: liveSelectGate,
                expectedId: task.id,
            });
            if (!resolved) return;
            await onSelectRef.current(resolved);
        } catch (error) {
            if (!canvasGenerationHistorySelectStillValid(captured, liveSelectGate())) return;
            setSelectionError(error instanceof Error ? error.message : "生成结果无法插入画布，请重试");
        } finally {
            if (selectionRequest.current === request) {
                selectionRequest.current = null;
                setSelectingId(null);
            }
        }
    };

    return (
        <Modal open={open} title="从生成历史选择" footer={null} onCancel={() => { cancelSelection(); onClose(); }} width={720} destroyOnHidden>
            <Input allowClear prefix={<Search className="size-3.5" />} value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索提示词或模型" aria-label="搜索生成历史" />
            {selectionError && <p role="alert" className="mt-2 text-xs text-destructive">{selectionError}</p>}
            {query.isError && tasks.length > 0 && <p role="alert" className="mt-2 text-xs text-destructive">生成历史刷新失败，请稍后重试。</p>}
            <div className="mt-3 max-h-[min(560px,65vh)] overflow-y-auto pr-1">
                {query.isLoading ? <div className="grid min-h-40 place-items-center"><Spin /></div> : tasks.length ? (
                    <div className="grid gap-2 grid-cols-3">
                        {tasks.map((task) => <HistoryTaskCard key={`${scope}:${projectId}:${task.id}`} task={task} active={open} selecting={selectingId === task.id} disabled={selectingId !== null} onSelect={() => void selectSummary(task)} />)}
                    </div>
                ) : (
                    <div className="grid min-h-24 place-items-center rounded-lg border border-dashed border-border/70 text-sm text-foreground/55">
                        {query.isError ? "生成历史暂时无法读取" : "暂无可插入的生成结果"}
                    </div>
                )}
            </div>
        </Modal>
    );
}

function HistoryTaskCard({ task, active, onSelect, selecting, disabled }: { task: GenerationTask; active: boolean; onSelect: () => void; selecting: boolean; disabled: boolean }) {
    const mode = generationTaskMode(task);
    const preview = generationHistoryPreviewImageSrc(task);
    const storageKey = generationHistoryPreviewStorageKey(task);
    const [hovered, setHovered] = useState(false);
    const [focused, setFocused] = useState(false);
    const Icon = mode === "video" ? FileVideo : mode === "audio" ? FileAudio : ImageIcon;
    const iconFallback = <div className="grid size-full place-items-center text-foreground/45"><Icon className="size-7" /></div>;
    return (
        <article role="button" tabIndex={0} aria-disabled={disabled} aria-busy={selecting} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} onFocus={() => setFocused(true)} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false); }} className={`group overflow-hidden rounded-lg border border-border/70 bg-surface text-left transition hover:border-foreground/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${disabled ? "cursor-wait" : "cursor-pointer"}`} onClick={() => { if (!disabled) onSelect(); }} onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); if (!disabled) onSelect(); } }} aria-label={`插入${modeLabel(mode)}：${task.prompt.slice(0, 40)}`}>
            <div className="relative aspect-video overflow-hidden bg-surface-tertiary">
                {storageKey ? (
                    <CachedResourceImage storageKey={storageKey} alt="生成结果预览" loading="lazy" className="size-full object-cover" fallback={iconFallback} loadingFallback={iconFallback} />
                ) : preview ? (
                    <img src={preview} alt="生成结果预览" loading="lazy" className="size-full object-cover" />
                ) : iconFallback}
                {mode === "video" && active && (hovered || focused) && <HistoryVideoPreview task={task} />}
                <span className="absolute bottom-1 left-1 rounded bg-black/65 px-1.5 py-0.5 text-[10px] text-white">{modeLabel(mode)}</span>
                {selecting && <div className="absolute inset-0 grid place-items-center bg-black/35"><Spin size="small" /></div>}
            </div>
            <div className="truncate px-2 py-2 text-xs text-foreground/80" title={task.prompt}>{task.prompt || "无提示词"}</div>
        </article>
    );
}

function HistoryVideoPreview({ task }: { task: GenerationTask }) {
    const [storageKey, setStorageKey] = useState("");
    const [error, setError] = useState("");
    const [retry, setRetry] = useState(0);
    const videoRef = useRef<HTMLVideoElement>(null);
    useEffect(() => {
        let cancelled = false;
        const controller = new AbortController();
        const expectedScope = captureUserScope();
        setStorageKey("");
        setError("");
        void queryGenerationTask(task.id, { signal: controller.signal, expectedScope }).then(detail => {
            if (cancelled || !userScopeMatches(expectedScope)) return;
            const checked = assertCanvasGenerationHistoryTaskForInsert(detail, { expectedId: task.id, projectId: task.projectId || "" });
            const key = generationHistoryVideoStorageKey(checked);
            if (!key) throw new Error("缺少可读取的视频资源");
            setStorageKey(key);
        }).catch(error => {
            if (!cancelled && userScopeMatches(expectedScope)) setError(error instanceof Error ? error.message : "预览加载失败，请重新加载");
        });
        return () => { cancelled = true; controller.abort(); };
    }, [task.id, task.projectId, retry]);
    const playback = useResourceVideoPlayback(storageKey, "", Boolean(storageKey));
    useEffect(() => {
        const video = videoRef.current;
        return () => video?.pause();
    }, [playback.url]);
    if (error || playback.error) return <div role="status" className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/75 p-2 text-xs text-white"><span>{error || playback.error}</span><Button size="small" type="text" className="text-white" onClick={(event) => { event.stopPropagation(); if (error) setRetry((value) => value + 1); else playback.retry(); }}>重新加载</Button></div>;
    if (!playback.url) return <div role="status" className="absolute inset-0 grid place-items-center bg-black/25"><Spin size="small" /></div>;
    return <video ref={videoRef} src={playback.url} aria-label="生成视频预览" muted playsInline autoPlay loop preload="metadata" className="absolute inset-0 size-full object-cover" onError={() => { if (!playback.requestCompatible()) playback.fail(); }} />;
}

export function generationHistoryVideoStorageKey(task: GenerationTask) {
    if (generationTaskMode(task) !== "video") return "";
    try {
        const result = JSON.parse(task.resultJson || "{}") as { video?: { storageKey?: string; url?: string; dataUrl?: string } };
        const id = resourceIdFromStorageKey(result.video?.storageKey) || ownedResourceIdFromMediaRef(result.video?.storageKey, result.video?.url || result.video?.dataUrl || task.previewUrl);
        return id ? resourceStorageKey(id) : "";
    } catch { return ""; }
}

function modeLabel(mode: string) {
    return mode === "video" ? "视频" : mode === "audio" ? "音频" : "图片";
}

export function generationHistoryPreviewImageSrc(task: GenerationTask) {
    const mode = generationTaskMode(task);
    if (mode === "audio") return "";
    const inline = mode === "video"
        ? inlineImagePreviewSrc(task.previewPosterUrl || previewPosterFromResult(task))
        : inlineImagePreviewSrc(task.previewPosterUrl || task.previewUrl || previewFromResult(task));
    if (generationHistoryPreviewStorageKey(task) && !inline.startsWith("data:image/")) return "";
    return inline;
}

export function generationHistoryPreviewStorageKey(task: GenerationTask) {
    const mode = generationTaskMode(task);
    if (mode === "audio") return "";
    if (mode === "video") {
        const poster = task.previewPosterUrl || previewPosterFromResult(task);
        const resourceId = ownedResourceIdFromMediaRef(undefined, poster);
        return resourceId ? resourceStorageKey(resourceId) : "";
    }
    return imagePreviewStorageKey(task);
}

function inlineImagePreviewSrc(value: string) {
    if (!value || ownedResourceIdFromMediaRef(undefined, value)) return "";
    return isImagePreviewSrc(value) ? value : "";
}

function imagePreviewStorageKey(task: GenerationTask) {
    const preview = task.previewPosterUrl || task.previewUrl || previewFromResult(task);
    if (!task.resultJson) {
        const resourceId = ownedResourceIdFromMediaRef(undefined, preview);
        return resourceId ? resourceStorageKey(resourceId) : "";
    }
    try {
        const result = JSON.parse(task.resultJson) as { images?: Array<{ storageKey?: string; dataUrl?: string; url?: string }> };
        const image = result.images?.[0];
        const resourceId = resourceIdFromStorageKey(image?.storageKey) || ownedResourceIdFromMediaRef(image?.storageKey, image?.dataUrl || image?.url || preview);
        return resourceId ? resourceStorageKey(resourceId) : "";
    } catch {
        const resourceId = ownedResourceIdFromMediaRef(undefined, preview);
        return resourceId ? resourceStorageKey(resourceId) : "";
    }
}

function isImagePreviewSrc(value: string) {
    if (!value) return false;
    const lower = value.toLowerCase();
    if (lower.startsWith("data:image/")) return true;
    if (lower.startsWith("data:")) return false;
    if (/\.(mp3|wav|m4a|aac|ogg|flac|mp4|webm|mov|mkv)(?:\?|$)/i.test(lower)) return false;
    return true;
}

function previewPosterFromResult(task: GenerationTask) {
    if (!task.resultJson) return "";
    try {
        const result = JSON.parse(task.resultJson) as { video?: { previewUrl?: string; posterUrl?: string } };
        return result.video?.previewUrl || result.video?.posterUrl || "";
    } catch {
        return "";
    }
}

function previewFromResult(task: GenerationTask) {
    if (!task.resultJson) return "";
    try {
        const result = JSON.parse(task.resultJson) as { images?: Array<{ dataUrl?: string; url?: string }>; video?: { previewUrl?: string; dataUrl?: string; url?: string }; audio?: { dataUrl?: string; url?: string } };
        const mode = generationTaskMode(task);
        if (mode === "image") return result.images?.[0]?.dataUrl || result.images?.[0]?.url || "";
        if (mode === "video") return result.video?.previewUrl || "";
        return "";
    } catch {
        return "";
    }
}
