import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Component, lazy, Suspense, useState, type ReactNode, type PointerEvent as ReactPointerEvent } from "react";
import { LoaderCircle, ScanFace, Sparkles, X } from "lucide-react";

import { SpotlightSurface } from "@/components/ui/aceternity/spotlight-surface";
import { aceternityMotion } from "@/lib/aceternity-motion";
import { canvasThemes } from "@/lib/canvas-theme";
import {
    canvasEmotionPresets,
    type CanvasEmotionParams,
    type CanvasEmotionPreset,
    type CanvasEmotionEditRegion,
    type CanvasFaceBox,
} from "@/lib/canvas/canvas-emotion";
import { useActiveTheme } from "@/stores/canvas/use-canvas-theme-store";

export type CanvasImageEmotionPayload = CanvasEmotionParams & {
    label: string;
    prompt: string;
    sourceDataUrl: string;
    maskDataUrl: string;
    characterDataUrl: string;
    editRegion: CanvasEmotionEditRegion;
    imageWidth: number;
    imageHeight: number;
};

export type CanvasEmotionCharacter = {
    id: string;
    name: string;
    faceBox: CanvasFaceBox;
};

type CanvasNodeEmotionPanelProps = {
    dataUrl: string;
    imageWidth: number;
    imageHeight: number;
    characters: CanvasEmotionCharacter[];
    activeCharacterId: string;
    preset: CanvasEmotionPreset;
    generating: boolean;
    error?: string;
    onSelectCharacter: (characterId: string) => void;
    onManualSelect: () => void;
    onPresetChange: (preset: CanvasEmotionPreset) => void;
    onClose: () => void;
    onConfirm: () => void;
};

export function CanvasNodeEmotionPanel({ dataUrl, imageWidth, imageHeight, characters, activeCharacterId, preset, generating, error, onSelectCharacter, onManualSelect, onPresetChange, onClose, onConfirm }: CanvasNodeEmotionPanelProps) {
    const theme = canvasThemes[useActiveTheme()];
    const reducedMotion = useReducedMotion();
    return (
        <SpotlightSurface
            data-canvas-no-zoom
            spotlightColor={theme.toolbar.itemHover}
            initial={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 8, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 5, scale: 0.98 }}
            transition={reducedMotion ? { duration: 0 } : aceternityMotion.spring.panel}
            className="aceternity-floating-panel w-[580px] max-w-full overflow-hidden rounded-[var(--r-2xl)] border backdrop-blur-2xl"
            style={{ background: theme.spatial.elevated, borderColor: theme.toolbar.border, color: theme.node.text, boxShadow: `0 28px 80px ${theme.spatial.shadow}` }}
        >
            <div className="flex h-11 items-center gap-1.5 border-b px-2.5" style={{ borderColor: theme.toolbar.border }}>
                <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
                    {characters.map((character) => (
                        <motion.button
                            key={character.id}
                            type="button"
                            layout
                            whileTap={reducedMotion ? undefined : { scale: 0.96 }}
                            className="relative flex h-8 shrink-0 items-center gap-1.5 rounded-[var(--dock-item-radius)] border px-1.5 pr-2 text-[var(--fs-label)] font-medium outline-none"
                            style={{ background: activeCharacterId === character.id ? theme.toolbar.activeBg : theme.spatial.surface, borderColor: activeCharacterId === character.id ? theme.spatial.glowStrong : theme.toolbar.border }}
                            onClick={() => onSelectCharacter(character.id)}
                        >
                            {activeCharacterId === character.id ? <motion.span layoutId="emotion-active-character" className="absolute inset-0 -z-10 rounded-[var(--r-md)]" style={{ boxShadow: `inset 0 0 0 1px ${theme.accent.primarySoft}` }} transition={aceternityMotion.spring.dock} /> : null}
                            <FaceThumbnail dataUrl={dataUrl} imageWidth={imageWidth} imageHeight={imageHeight} box={character.faceBox} />
                            <span>{character.name}</span>
                        </motion.button>
                    ))}
                    <button type="button" className="flex h-8 shrink-0 items-center gap-1.5 rounded-[var(--dock-item-radius)] border px-2 text-[var(--fs-label)] font-medium opacity-70 transition hover:opacity-100" style={{ background: theme.spatial.surface, borderColor: theme.toolbar.border }} onClick={onManualSelect}>
                        <ScanFace className="size-3.5" />手动框选
                    </button>
                </div>
                <button type="button" aria-label="关闭情绪调节" className="grid size-7 shrink-0 place-items-center rounded-full transition hover:bg-black/5 dark:hover:bg-white/10" onClick={onClose}><X className="size-3.5" /></button>
            </div>

            <div className="grid h-[216px] grid-cols-[minmax(0,1fr)_212px] gap-2.5 p-2.5">
                <EmotionPreviewBoundary>
                    <Suspense fallback={<EmotionPreviewState />}><EmotionHeadPreview preset={preset} /></Suspense>
                </EmotionPreviewBoundary>
                <EmotionPad preset={preset} onChange={onPresetChange} />
            </div>

            <div className="flex min-h-11 items-center gap-2 border-t px-3" style={{ borderColor: theme.toolbar.border }}>
                <span className="text-[var(--fs-tiny)]" style={{ color: theme.node.muted }}>情绪定位</span>
                <AnimatePresence mode="wait" initial={false}>
                    <motion.span key={preset.id} initial={reducedMotion ? false : { opacity: 0, y: 4, filter: "blur(4px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} exit={reducedMotion ? undefined : { opacity: 0, y: -3, filter: "blur(3px)" }} transition={{ duration: reducedMotion ? 0 : 0.18 }} className="text-xs font-semibold">{preset.label}</motion.span>
                </AnimatePresence>
                {error ? <span className="min-w-0 flex-1 truncate text-right text-[var(--fs-tiny)]" style={{ color: theme.accent.danger }} title={error}>{error}</span> : <span className="flex-1" />}
                <motion.button
                    type="button"
                    disabled={generating}
                    whileHover={reducedMotion || generating ? undefined : { y: -1 }}
                    whileTap={reducedMotion || generating ? undefined : { scale: 0.97 }}
                    className="flex h-8 shrink-0 items-center gap-1.5 rounded-[var(--dock-item-radius)] px-3 text-[var(--fs-label)] font-semibold disabled:cursor-wait disabled:opacity-55"
                    style={{ background: theme.node.activeStroke, color: theme.node.panel }}
                    onClick={onConfirm}
                >
                    <Sparkles className={`size-3.5 ${generating ? "animate-pulse" : ""}`} />{generating ? "准备生成" : "生成"}
                </motion.button>
            </div>
        </SpotlightSurface>
    );
}

function FaceThumbnail({ dataUrl, imageWidth, imageHeight, box }: { dataUrl: string; imageWidth: number; imageHeight: number; box: CanvasFaceBox }) {
    const scaleX = imageWidth / Math.max(1, box.width);
    const scaleY = imageHeight / Math.max(1, box.height);
    return (
        <span className="relative block size-6 shrink-0 overflow-hidden rounded-[var(--r-sm)] bg-black/20">
            <img src={dataUrl} alt="" draggable={false} className="pointer-events-none absolute max-w-none" style={{ width: `${scaleX * 100}%`, height: `${scaleY * 100}%`, left: `${-(box.x / Math.max(1, box.width)) * 100}%`, top: `${-(box.y / Math.max(1, box.height)) * 100}%` }} />
        </span>
    );
}

function EmotionPad({ preset, onChange }: { preset: CanvasEmotionPreset; onChange: (preset: CanvasEmotionPreset) => void }) {
    const theme = canvasThemes[useActiveTheme()];
    const reducedMotion = useReducedMotion();
    const [pointer, setPointer] = useState<{ x: number; y: number } | null>(null);
    const [dragging, setDragging] = useState(false);
    const update = (event: ReactPointerEvent<HTMLDivElement>) => {
        const rect = event.currentTarget.getBoundingClientRect();
        const x = Math.max(0, Math.min(4, ((event.clientX - rect.left) / Math.max(1, rect.width)) * 5 - 0.5));
        const y = Math.max(0, Math.min(4, ((event.clientY - rect.top) / Math.max(1, rect.height)) * 5 - 0.5));
        setPointer({ x, y });
        const next = canvasEmotionPresets[Math.round(y) * 5 + Math.round(x)];
        if (next && next.id !== preset.id) onChange(next);
    };
    const selectedColumn = 2 - preset.intimacy;
    const selectedRow = 2 - preset.arousal;
    return (
        <div className="relative rounded-[var(--r-lg)] border px-[25px] pb-[22px] pt-[24px]" style={{ background: theme.toolbar.itemHover, borderColor: theme.toolbar.border }}>
            <span className="pointer-events-none absolute inset-x-0 top-1.5 text-center text-[var(--fs-micro)]" style={{ color: theme.node.muted }}>激动</span>
            <span className="pointer-events-none absolute inset-x-0 bottom-1.5 text-center text-[var(--fs-micro)]" style={{ color: theme.node.muted }}>平静</span>
            <span className="pointer-events-none absolute left-1 top-1/2 -translate-y-1/2 text-[var(--fs-micro)] [writing-mode:vertical-rl]" style={{ color: theme.node.muted }}>亲近</span>
            <span className="pointer-events-none absolute right-1 top-1/2 -translate-y-1/2 text-[var(--fs-micro)] [writing-mode:vertical-rl]" style={{ color: theme.node.muted }}>疏离</span>
            <div
                role="slider"
                aria-label="情绪强度"
                aria-valuetext={preset.label}
                tabIndex={0}
                className="relative grid size-full touch-none cursor-crosshair grid-cols-5 grid-rows-5 outline-none"
                onPointerDown={(event) => { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); setDragging(true); update(event); }}
                onPointerMove={(event) => { if (dragging) update(event); }}
                onPointerUp={(event) => { setDragging(false); setPointer(null); event.currentTarget.releasePointerCapture(event.pointerId); }}
                onPointerCancel={() => { setDragging(false); setPointer(null); }}
                onKeyDown={(event) => {
                    const column = Math.max(0, Math.min(4, selectedColumn + (event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0)));
                    const row = Math.max(0, Math.min(4, selectedRow + (event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0)));
                    if (column !== selectedColumn || row !== selectedRow) { event.preventDefault(); onChange(canvasEmotionPresets[row * 5 + column]); }
                }}
            >
                {canvasEmotionPresets.map((item, index) => {
                    const column = index % 5;
                    const row = Math.floor(index / 5);
                    const active = item.id === preset.id;
                    const onPath = !dragging && (column === selectedColumn || row === selectedRow);
                    const distance = pointer ? Math.hypot(pointer.x - column, pointer.y - row) : 4;
                    const proximity = dragging ? Math.max(0, 1 - distance / 1.7) : 0;
                    return (
                        <button key={item.id} type="button" aria-label={item.label} title={item.label} className="relative m-auto grid size-7 place-items-center rounded-full outline-none" onClick={() => onChange(item)}>
                            <motion.span
                                animate={{ scale: active ? 1.65 : 1 + proximity * 0.42, opacity: active ? 1 : onPath ? 0.88 : 0.42 + proximity * 0.45 }}
                                transition={reducedMotion ? { duration: 0 } : aceternityMotion.spring.dock}
                                className={`block rounded-full ${active ? "size-3 border-2 bg-transparent" : "size-2 bg-current"}`}
                                style={{ color: active || onPath ? theme.node.activeStroke : theme.node.muted, borderColor: theme.node.activeStroke, boxShadow: active ? `0 0 0 5px ${theme.accent.primarySoft}, 0 0 18px ${theme.spatial.glowStrong}` : undefined }}
                            />
                        </button>
                    );
                })}
                {dragging && pointer ? <motion.span className="pointer-events-none absolute size-8 -translate-x-1/2 -translate-y-1/2 rounded-full border" style={{ left: `${10 + pointer.x * 20}%`, top: `${10 + pointer.y * 20}%`, borderColor: theme.spatial.glowStrong, boxShadow: `0 0 18px ${theme.spatial.glow}` }} /> : null}
            </div>
        </div>
    );
}

const EmotionHeadPreview = lazy(() => import("./canvas-emotion-head-preview").then(module => ({ default: module.EmotionHeadPreview })));

function EmotionPreviewState({ failed = false }: { failed?: boolean }) {
    const theme = canvasThemes[useActiveTheme()];
    return <div className="relative grid place-items-center overflow-hidden rounded-[var(--r-lg)] border p-3 text-center text-xs" style={{ background: "#26272a", borderColor: theme.toolbar.border, color: theme.node.muted }} role={failed ? "alert" : "status"}>
        {failed ? "预览加载失败，请刷新页面后重试。" : <span><LoaderCircle className="mx-auto mb-2 size-4 animate-spin" aria-hidden />正在加载预览…</span>}
    </div>;
}

class EmotionPreviewBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
    state = { failed: false };
    static getDerivedStateFromError() { return { failed: true }; }
    render() { return this.state.failed ? <EmotionPreviewState failed /> : this.props.children; }
}
