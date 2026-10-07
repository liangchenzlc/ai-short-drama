type CanvasInput = {
    nodes: readonly { id: string }[];
    connections: readonly unknown[];
    chatSessions: readonly unknown[];
    activeChatId: unknown;
    viewport: unknown;
};

type Options = {
    canvasId: string;
    getInput: () => CanvasInput;
    selectedNodeIds: readonly string[];
    assertCurrent: () => void;
    flushPreferences: () => Promise<void>;
    preferences: () => { dirty: boolean; status: string; error?: string };
    saveCanvas: () => Promise<boolean>;
    flushLocal: () => Promise<void>;
    hasUnconfirmedEdits: () => boolean;
    revision: () => string | undefined;
};

/** 上下文只使用服务端确认的作品；等待期间产生的新编辑保留为本机草稿。 */
export async function prepareCanvasAssistantContext(options: Options) {
    options.assertCurrent();
    const input = options.getInput();
    await options.flushPreferences();
    options.assertCurrent();
    const preferences = options.preferences();
    if (preferences.dirty || preferences.status === "error") {
        throw new Error(preferences.error || "模型偏好尚未保存，请处理后再发送");
    }
    if (!await options.saveCanvas()) throw new Error("画布尚未保存，消息草稿已保留，请处理保存问题后再发送");
    options.assertCurrent();
    const latest = options.getInput();
    const changed = input.nodes !== latest.nodes || input.connections !== latest.connections
        || input.chatSessions !== latest.chatSessions || input.activeChatId !== latest.activeChatId
        || input.viewport !== latest.viewport;
    const currentPreferences = options.preferences();
    if (changed || currentPreferences.dirty || currentPreferences.status === "error" || options.hasUnconfirmedEdits()) {
        await options.flushLocal();
        options.assertCurrent();
        throw new Error("保存期间内容再次变化，消息与作品草稿已保留，请等待保存完成后再发送");
    }
    const revision = options.revision();
    if (!revision || !/^[1-9]\d*$/.test(revision)) throw new Error("画布服务端版本尚未确认，请重新载入后发送");
    const available = new Set(latest.nodes.map(node => node.id));
    return {
        kind: "canvas" as const, id: options.canvasId, revision,
        selected: [...new Set(options.selectedNodeIds)].filter(id => available.has(id)).map(id => ({ kind: "node" as const, id })),
    };
}
