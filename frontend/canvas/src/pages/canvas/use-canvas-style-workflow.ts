import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { App } from "antd";

import type { CanvasStylePreset } from "@/components/canvas/canvas-style-picker-modal";
import { createCanvasNode } from "@/lib/canvas/canvas-project-domain";
import { createStyleProfileSnapshot, serializeStyleProfile } from "@/lib/canvas/style-profile";
import { CanvasNodeType, type CanvasNodeData, type CanvasNodeMetadata, type Position } from "@/types/canvas";
import { getActiveUserScope } from "@/lib/user-scope";

type UseCanvasStyleWorkflowOptions = {
    canvasId: string;
    nodesRef: { current: CanvasNodeData[] };
    selectedNodeIdsRef: { current: Set<string> };
    getCanvasCenter: () => Position;
    setNodes: Dispatch<SetStateAction<CanvasNodeData[]>>;
    setSelectedNodeIds: Dispatch<SetStateAction<Set<string>>>;
    setSelectedConnectionId: Dispatch<SetStateAction<string | null>>;
    setDialogNodeId: Dispatch<SetStateAction<string | null>>;
    setStylePickerOpen: Dispatch<SetStateAction<boolean>>;
};

export function useCanvasStyleWorkflow({ canvasId, nodesRef, selectedNodeIdsRef, getCanvasCenter, setNodes, setSelectedNodeIds, setSelectedConnectionId, setDialogNodeId, setStylePickerOpen }: UseCanvasStyleWorkflowOptions) {
    const { message } = App.useApp();
    const liveContext = useRef({ canvasId, mounted: true });
    liveContext.current = { canvasId, mounted: liveContext.current.mounted };
    const applyingRef = useRef(false);
    const [styleApplying, setStyleApplying] = useState(false);
    useEffect(() => { liveContext.current.mounted = true; return () => { liveContext.current.mounted = false; }; }, []);

    const applyCanvasStyle = useCallback(
        (preset: CanvasStylePreset, profileJson: string) => {
            const current = nodesRef.current.find((node) => node.type === CanvasNodeType.Text && node.metadata?.workflowKind === "styleboard");
            const metadata: CanvasNodeMetadata = {
                content: preset.prompt,
                prompt: preset.prompt,
                status: "success",
                workflowKind: "styleboard",
                workflowTitle: "项目画风",
                workflowDescription: preset.description,
                stylePresetId: preset.id,
                styleProfileJson: profileJson,
                fontSize: 14,
            };
            let styleNode: CanvasNodeData;
            if (current) {
                styleNode = { ...current, title: `画风 · ${preset.title}`, metadata: { ...current.metadata, ...metadata } };
                nodesRef.current = nodesRef.current.map((node) => (node.id === current.id ? styleNode : node));
            } else {
                styleNode = createCanvasNode(CanvasNodeType.Text, getCanvasCenter(), metadata);
                styleNode.title = `画风 · ${preset.title}`;
                styleNode.width = 420;
                styleNode.height = 240;
                nodesRef.current = [...nodesRef.current, styleNode];
            }
            setNodes(nodesRef.current);
            const selection = new Set([styleNode.id]);
            selectedNodeIdsRef.current = selection;
            setSelectedNodeIds(selection);
            setSelectedConnectionId(null);
            setDialogNodeId(null);
            setStylePickerOpen(false);
            message.success(`已应用“${preset.title}”画风`);
        },
        [getCanvasCenter, message, nodesRef, selectedNodeIdsRef, setDialogNodeId, setNodes, setSelectedConnectionId, setSelectedNodeIds, setStylePickerOpen],
    );

    const applyCanvasStyleAsync = useCallback(async (preset: CanvasStylePreset) => {
        if (applyingRef.current) throw new Error("画风正在保存，请稍后重试");
        const scope = getActiveUserScope();
        applyingRef.current = true;
        setStyleApplying(true);
        try {
            const profile = preset.profile || createStyleProfileSnapshot(preset);
            const profileJson = serializeStyleProfile(profile);
            if (!liveContext.current.mounted || liveContext.current.canvasId !== canvasId || getActiveUserScope() !== scope) throw new Error("页面或账号已变化，未向当前画布应用画风");
            applyCanvasStyle(preset, profileJson);
        } finally { applyingRef.current = false; if (liveContext.current.mounted) setStyleApplying(false); }
    }, [canvasId, applyCanvasStyle]);

    const selectCanvasStyle = useCallback(
        (preset: CanvasStylePreset) => {
            void applyCanvasStyleAsync(preset).catch((error) => message.error(error instanceof Error ? error.message : "项目画风保存失败"));
        },
        [applyCanvasStyleAsync, message],
    );

    const syncCanvasStyle = useCallback((preset: CanvasStylePreset) => applyCanvasStyle(preset, serializeStyleProfile(preset.profile || createStyleProfileSnapshot(preset))), [applyCanvasStyle]);
    return { selectCanvasStyle, applyCanvasStyleAsync, syncCanvasStyle, styleApplying };
}
