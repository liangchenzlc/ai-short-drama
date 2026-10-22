import type { CanvasNodeData } from "@/types/canvas";

export type StoryboardGenerationContext = {
    projectStyle: {
        presetId: string;
        title: string;
        prompt: string;
        profileJson?: string;
    };
    characters: Array<{
        nodeId: string;
        name: string;
        definition: Record<string, unknown>;
    }>;
};

// 分镜的两条入口共用同一强校验，避免画风或角色版本在某条旁路里被遗漏。
export function resolveStoryboardGenerationContext(nodes: CanvasNodeData[]): StoryboardGenerationContext {
    const styleNode = nodes.find((node) => node.metadata?.workflowKind === "styleboard");
    const stylePrompt = String(styleNode?.metadata?.content || styleNode?.metadata?.prompt || "").trim();
    const stylePresetId = String(styleNode?.metadata?.stylePresetId || "").trim();
    if (!styleNode || !stylePrompt || !stylePresetId) throw new Error("请先设置项目画风，再生成分镜");

    const characterNodes = nodes.filter(node => node.metadata?.workflowKind === "character" && node.metadata.characterDefinition);
    const invalid = characterNodes.find(node => !(node.metadata?.characterName || node.title).trim());
    if (invalid) throw new Error("请先填写角色名称，再生成分镜");

    return {
        projectStyle: {
            presetId: stylePresetId,
            title: styleNode.title.replace(/^(?:项目)?画风\s*[·：:]?\s*/, "").trim() || styleNode.title,
            prompt: stylePrompt,
            profileJson: styleNode.metadata?.styleProfileJson,
        },
        characters: characterNodes.map((node) => ({
            nodeId: node.id,
            name: (node.metadata?.characterName || node.title).trim(),
            definition: node.metadata?.characterDefinition || { prompt: node.metadata?.characterPrompt || "" },
        })),
    };
}

// Expose the same generation prerequisites to the Agent before it starts a workflow.
export function inspectStoryboardReadiness(nodes: CanvasNodeData[]) {
    let generationContext: StoryboardGenerationContext | undefined;
    let blockingReason: string | undefined;
    try { generationContext = resolveStoryboardGenerationContext(nodes); }
    catch (error) { blockingReason = error instanceof Error ? error.message : "分镜上下文未就绪"; }
    const styleNode = nodes.find((node) => node.metadata?.workflowKind === "styleboard");
    const styleReady = Boolean(styleNode?.metadata?.stylePresetId?.trim() && String(styleNode.metadata.content || styleNode.metadata.prompt || "").trim());
    const storyboards = nodes.filter((node) => node.type === "script").map((node) => {
        const rows = node.metadata?.storyboard?.rows || [];
        const incompleteRowIds = rows.filter((row) => !Number.isFinite(row.durationSeconds) || row.durationSeconds <= 0 || !row.videoMotionPrompt?.trim()).map((row) => row.id);
        return { nodeId: node.id, title: node.title, rowCount: rows.length, populated: rows.length > 0 && incompleteRowIds.length === 0, incompleteRowIds, totalDurationSeconds: rows.reduce((total, row) => total + (Number.isFinite(row.durationSeconds) && row.durationSeconds > 0 ? row.durationSeconds : 0), 0) };
    });
    return {
        canGenerateStoryboard: Boolean(generationContext), blockingReason,
        style: { ready: styleReady, nodeId: styleNode?.id, presetId: styleNode?.metadata?.stylePresetId, title: styleNode?.title },
        storyboards,
        nextActions: [
            ...(!styleReady ? ["先 canvas_list_styles 读取真实画风，结合已确认风格调用 canvas_apply_style；该工具会复用或创建画风节点。仅在缺少关键风格取舍时询问用户。"] : []),
            ...(storyboards.some((item) => !item.populated) ? ["先 canvas_read_storyboard 读取真实行 ID，再 canvas_edit_storyboard 更新空行并按需新增镜头；正文或标题不代表已填充分镜表。"] : []),
        ],
    };
}
